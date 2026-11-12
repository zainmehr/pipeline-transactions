"""Point d'entrée de la pipeline : orchestre toutes les étapes.

    [1/6] Génération des fichiers       (options --generate N et --images N)
    [2/6] Envoi vers le stockage S3     (option --upload) : bucket, envoi, vérification
    [3/6] Préparation de la base        (option --save) : schéma + fichiers déjà traités
    [4/6] Récupération des fichiers     (option --source local|s3)
    [5/6] Chargement et traitement      : CSV lus directement, images lues par OCR,
                                          puis validation de tous les nouveaux fichiers
    [6/6] Insertion en base             (option --save)

Avec --upload-only, la pipeline s'arrête après l'envoi vers S3 : c'est le rôle d'un
producteur de données, le traitement étant lancé séparément (ex. CronJob Kubernetes).
Avec --allow-empty, une source vide n'est pas une erreur : un traitement planifié qui ne
trouve aucun nouveau fichier se termine normalement.

Un fichier déjà traité (même contenu, donc même hash) est ignoré : la pipeline peut être
relancée autant de fois que nécessaire sans jamais compter une transaction deux fois.
Si un seul fichier est invalide ou irrécupérable, rien n'est inséré et la commande
retourne le code 1.

Les accès à la base et au stockage S3 sont réessayés en cas d'erreur temporaire
(service indisponible, connexion perdue, ...). Au-delà du nombre maximal de tentatives,
la pipeline s'arrête ; les fichiers déjà insérés sont conservés et une relance reprend
là où elle s'était arrêtée.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from pipeline.generation.generator import DEFAULT_SEED, generate_files
from pipeline.generation.images import generate_image_files
from pipeline.loading.csv_loader import InvalidCSVError
from pipeline.loading.ocr import OCRUnavailableError, check_tesseract
from pipeline.loading.parsers import content_type, parse_file, source_type
from pipeline.loading.sources import (
    SourceFile,
    exclude_processed,
    fetch_s3_files,
    read_local_files,
)
from pipeline.models import ProcessingResult, Transaction
from pipeline.processing.transformations import FLAG_THRESHOLD, process_transactions
from pipeline.retry import RetryError, RetryPolicy, retry
from pipeline.storage.database import (
    DatabaseConfig,
    DuplicateFileError,
    apply_schema,
    connect,
    fetch_processed_hashes,
    insert_processed_file,
)
from pipeline.storage.s3 import (
    TRANSIENT_S3_ERRORS,
    ObjectNotFoundError,
    S3Config,
    TransientS3Error,
    create_client,
    ensure_bucket,
    list_keys,
    object_hash,
    object_key,
    upload_file,
)

# Erreurs considérées comme temporaires, donc réessayées : connexion impossible ou perdue,
# serveur redémarré, conflit de transaction... (toutes héritent de OperationalError).
TRANSIENT_DB_ERRORS: tuple[type[BaseException], ...] = (psycopg.OperationalError,)

SOURCES = ("local", "s3")


class PipelineError(Exception):
    """Erreur qui arrête la pipeline ; son message est affiché à l'utilisateur."""


class SimulatedFailures:
    """Provoque volontairement l'échec des N prochaines tentatives d'une opération,
    pour démontrer le mécanisme de retry (options --simulate-*-failures)."""

    def __init__(self, count: int, make_error: Callable[[str], BaseException]) -> None:
        self.remaining = count
        self.make_error = make_error

    def trigger(self, label: str) -> None:
        """Lève l'erreur simulée tant qu'il reste des échecs à provoquer."""
        if self.remaining > 0:
            self.remaining -= 1
            raise self.make_error(label)

    def hook(self, label: str) -> Callable[[], None] | None:
        """Comme trigger, mais à appeler plus tard (ex. juste avant un commit SQL)."""
        if self.remaining <= 0:
            return None
        return lambda: self.trigger(label)


def insert_failures(count: int) -> SimulatedFailures:
    return SimulatedFailures(
        count,
        lambda name: psycopg.OperationalError(
            f"[simulation] connexion perdue pendant l'insertion de {name}"
        ),
    )


def download_failures(count: int) -> SimulatedFailures:
    return SimulatedFailures(
        count,
        lambda name: TransientS3Error(
            f"[simulation] stockage S3 indisponible pendant la récupération de {name}"
        ),
    )


@dataclass(frozen=True)
class LoadedFile:
    file: SourceFile
    transactions: list[Transaction]


# --- Affichage ---------------------------------------------------------------


TOTAL_STEPS = 6


def step(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_STEPS}] {title}")


def _print_totals(title: str, totals: Mapping[str, Decimal]) -> None:
    print(f"\n{title}")
    for key, amount in totals.items():
        print(f"  {key:<30} {amount:>12.2f}")


def print_report(result: ProcessingResult) -> None:
    flagged = [ft.transaction for ft in result.transactions if ft.is_flagged]
    print(f"\n{len(result.transactions)} transactions traitées")
    _print_totals("Montants envoyés par IBAN", result.sent_by_iban)
    _print_totals("Montants envoyés par banque", result.sent_by_bank)
    _print_totals("Montants reçus par IBAN", result.received_by_iban)
    print(f"\nTransactions flaggées (montant > {FLAG_THRESHOLD}) : {len(flagged)}")
    for tx in flagged:
        print(f"  {tx.datetime_transaction.isoformat()}  {tx.iban_origine}  {tx.montant:>10.2f}")


# --- Étapes ------------------------------------------------------------------


def read_local(directory: Path, allow_empty: bool = False) -> list[SourceFile]:
    try:
        files = read_local_files(directory)
    except FileNotFoundError as error:
        raise PipelineError(str(error)) from error
    if not files and not allow_empty:
        raise PipelineError(f"aucun fichier CSV ou image dans {directory}")
    return files


def upload_files(config: S3Config, files: Sequence[SourceFile], policy: RetryPolicy) -> None:
    """Crée le bucket si besoin, y envoie les fichiers, puis vérifie leur présence."""
    client = create_client(config)

    def call(func, description):
        return retry(func, policy, TRANSIENT_S3_ERRORS, description)

    created = call(lambda: ensure_bucket(client, config.bucket), "connexion au stockage S3")
    print(f"  bucket {config.describe()} {'créé' if created else 'déjà existant'}")

    for file in files:
        key = call(
            lambda file=file: upload_file(
                client,
                config.bucket,
                file.name,
                file.content,
                file.file_hash,
                content_type(file.name),
            ),
            f"envoi de {file.name}",
        )
        print(f"  {file.name} envoyé vers s3://{config.bucket}/{key}")

    # Vérification : chaque fichier est présent et son contenu est celui qui a été envoyé.
    present = set(call(lambda: list_keys(client, config.bucket), "liste des objets du bucket"))
    problems = []
    for file in files:
        key = object_key(file.name, file.file_hash)
        if key not in present:
            problems.append(f"{key} : absent du bucket")
            continue
        stored_hash = call(
            lambda key=key: object_hash(client, config.bucket, key), f"vérification de {key}"
        )
        if stored_hash != file.file_hash:
            problems.append(f"{key} : contenu différent du fichier envoyé")
    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise PipelineError(f"vérification du stockage S3 échouée :\n{details}")
    print(f"  vérification : {len(files)}/{len(files)} fichier(s) présent(s) et intègre(s)")


def prepare_database(config: DatabaseConfig, policy: RetryPolicy) -> set[str]:
    """Crée le schéma si besoin et retourne les hash des fichiers déjà traités."""

    def attempt() -> set[str]:
        with connect(config) as conn:
            apply_schema(conn)
            return fetch_processed_hashes(conn)

    hashes = retry(attempt, policy, TRANSIENT_DB_ERRORS, "connexion à la base")
    print(f"  base {config.describe()} prête")
    return hashes


def retrieve_from_s3(
    config: S3Config,
    processed_hashes: set[str],
    policy: RetryPolicy,
    failures: SimulatedFailures,
    allow_empty: bool = False,
) -> list[SourceFile]:
    print(f"  source : {config.describe()}")
    if failures.remaining:
        print(f"  [simulation] les {failures.remaining} prochains téléchargements échoueront")
    client = create_client(config)
    try:
        if allow_empty:
            # Traitement planifié : il peut démarrer avant le premier dépôt de fichiers.
            retry(
                lambda: ensure_bucket(client, config.bucket),
                policy,
                TRANSIENT_S3_ERRORS,
                "connexion au stockage S3",
            )
        files, total = fetch_s3_files(
            client, config.bucket, processed_hashes, policy, failures.trigger
        )
    except RetryError as error:
        raise PipelineError(
            f"stockage S3 inaccessible ({config.describe()}) : {error}\n"
            "Le traitement est arrêté : aucune donnée n'a été insérée. Relancer la pipeline "
            "une fois le stockage disponible."
        ) from error
    except (ObjectNotFoundError, ClientError, BotoCoreError) as error:
        raise PipelineError(
            f"erreur du stockage S3 ({config.describe()}) : {error}\n"
            "Le traitement est arrêté : aucune donnée n'a été insérée."
        ) from error
    if total == 0 and not allow_empty:
        raise PipelineError(f"aucun fichier CSV ou image dans {config.describe()}")
    return files


def load_files(files: Sequence[SourceFile]) -> list[LoadedFile]:
    """Décode et valide tous les fichiers (CSV, ou images lues par OCR) avant toute
    insertion."""
    if any(source_type(file.name) == "image" for file in files):
        try:
            print(f"  OCR : {check_tesseract()}")
        except OCRUnavailableError as error:
            raise PipelineError(f"{error}\nAucune donnée n'a été insérée.") from error
    loaded = []
    for file in files:
        try:
            transactions = parse_file(file.content, file.name)
        except InvalidCSVError as error:
            raise PipelineError(f"{error}\nAucune donnée n'a été insérée.") from error
        origin = " (extraites par OCR)" if source_type(file.name) == "image" else ""
        print(f"  {file.name} : {len(transactions)} transactions chargées{origin}")
        loaded.append(LoadedFile(file, transactions))
    return loaded


def insert_files(
    config: DatabaseConfig,
    files: Sequence[LoadedFile],
    policy: RetryPolicy,
    failures: SimulatedFailures,
) -> None:
    for loaded in files:
        name = loaded.file.name
        result = process_transactions(loaded.transactions)

        def attempt(loaded: LoadedFile = loaded, name: str = name, result=result) -> int:
            # Nouvelle connexion à chaque tentative : la précédente peut avoir été coupée.
            with connect(config) as conn:
                return insert_processed_file(
                    conn,
                    loaded.file.origin,
                    loaded.file.file_hash,
                    result,
                    before_commit=failures.hook(name),
                    source_type=source_type(name),
                )

        try:
            file_id = retry(attempt, policy, TRANSIENT_DB_ERRORS, f"insertion de {name}")
        except DuplicateFileError:
            # Le fichier est déjà en base : soit inséré entre-temps, soit validé par une
            # tentative dont la confirmation a été perdue. Il n'est pas compté deux fois.
            print(f"  {name} : déjà traité, ignoré")
            continue
        print(f"  {name} inséré (id={file_id}, {len(loaded.transactions)} transactions)")


def db_unavailable(config: DatabaseConfig, error: RetryError) -> PipelineError:
    return PipelineError(
        f"base de données inaccessible ({config.describe()}) : {error}\n"
        "Les fichiers déjà insérés sont conservés ; relancer la pipeline reprendra "
        "le traitement là où il s'est arrêté."
    )


def run(
    input_dir: Path,
    generate: int = 0,
    seed: int = DEFAULT_SEED,
    save: bool = False,
    policy: RetryPolicy | None = None,
    simulate_insert_failures: int = 0,
    upload: bool = False,
    source: str = "local",
    simulate_download_failures: int = 0,
    images: int = 0,
    upload_only: bool = False,
    allow_empty: bool = False,
) -> None:
    """Exécute la pipeline. Lève PipelineError en cas d'échec."""
    if source not in SOURCES:
        raise PipelineError(f"source inconnue : {source!r} (attendu : {', '.join(SOURCES)})")
    policy = policy or RetryPolicy()
    s3_config = S3Config.from_env()
    db_config = DatabaseConfig.from_env()

    step(1, "Génération des fichiers")
    if generate:
        for path in generate_files(input_dir, generate, seed):
            print(f"  {path.name} généré")
    if images:
        for path in generate_image_files(input_dir, images, seed):
            print(f"  {path.name} généré (image)")
    if not generate and not images:
        print("  ignorée (utiliser --generate N et/ou --images N)")

    step(2, "Envoi vers le stockage S3")
    if upload:
        try:
            upload_files(s3_config, read_local(input_dir), policy)
        except RetryError as error:
            raise PipelineError(
                f"stockage S3 inaccessible ({s3_config.describe()}) : {error}"
            ) from error
        except (ClientError, BotoCoreError) as error:
            raise PipelineError(
                f"erreur du stockage S3 ({s3_config.describe()}) : {error}"
            ) from error
    else:
        print("  ignoré (utiliser --upload)")

    if upload_only:
        print("\nTraitement non demandé (--upload-only) : les fichiers sont dans le stockage S3.")
        return

    step(3, "Préparation de la base")
    processed_hashes: set[str] = set()
    if save:
        try:
            processed_hashes = prepare_database(db_config, policy)
        except RetryError as error:
            raise db_unavailable(db_config, error) from error
    else:
        print("  ignorée (utiliser --save)")

    step(4, "Récupération des fichiers")
    if source == "s3":
        new_files = retrieve_from_s3(
            s3_config,
            processed_hashes,
            policy,
            download_failures(simulate_download_failures),
            allow_empty,
        )
    else:
        print(f"  source : dossier local {input_dir}")
        new_files = exclude_processed(read_local(input_dir, allow_empty), processed_hashes)

    step(5, "Chargement et traitement")
    loaded = load_files(new_files)
    if loaded:
        print_report(process_transactions([tx for lf in loaded for tx in lf.transactions]))
    else:
        print("  aucun nouveau fichier")

    step(6, "Insertion en base")
    if not save:
        print("  ignorée (utiliser --save)")
        return
    if not loaded:
        print("  aucun fichier à insérer")
        return
    if simulate_insert_failures:
        print(f"  [simulation] les {simulate_insert_failures} prochaines insertions échoueront")
    try:
        insert_files(db_config, loaded, policy, insert_failures(simulate_insert_failures))
    except RetryError as error:
        raise db_unavailable(db_config, error) from error


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()  # charge le fichier .env s'il existe (sans écraser l'environnement)
    parser = argparse.ArgumentParser(description="Pipeline de transactions financières.")
    parser.add_argument("--input", type=Path, default=Path("data"), help="Dossier des CSV")
    parser.add_argument(
        "--generate", type=int, default=0, metavar="N", help="Génère N fichiers avant traitement"
    )
    parser.add_argument(
        "--images",
        type=int,
        default=0,
        metavar="N",
        help="Génère N relevés au format image (lus par OCR) avant traitement",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Graine de génération")
    parser.add_argument(
        "--upload", action="store_true", help="Envoie les CSV dans le stockage S3 et vérifie"
    )
    parser.add_argument(
        "--source",
        choices=SOURCES,
        default="local",
        help="Origine des fichiers à traiter : dossier local ou bucket S3 (défaut : local)",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="S'arrête après l'envoi vers S3 (le traitement est lancé séparément)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Une source sans aucun fichier n'est pas une erreur (traitement planifié)",
    )
    parser.add_argument("--save", action="store_true", help="Insère les résultats en base")
    parser.add_argument(
        "--max-attempts", type=int, default=3, help="Nombre maximal de tentatives (défaut : 3)"
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="Attente avant la 2e tentative, doublée ensuite (défaut : 1 s)",
    )
    parser.add_argument(
        "--simulate-insert-failures",
        type=int,
        default=0,
        metavar="N",
        help="Démonstration : fait échouer les N premières tentatives d'insertion",
    )
    parser.add_argument(
        "--simulate-download-failures",
        type=int,
        default=0,
        metavar="N",
        help="Démonstration : fait échouer les N premières récupérations depuis S3",
    )
    args = parser.parse_args(argv)

    if args.upload_only and not args.upload:
        parser.error("--upload-only nécessite --upload")

    try:
        policy = RetryPolicy(max_attempts=args.max_attempts, initial_delay=args.retry_delay)
    except ValueError as error:
        parser.error(str(error))

    try:
        run(
            args.input,
            generate=args.generate,
            seed=args.seed,
            save=args.save,
            policy=policy,
            simulate_insert_failures=args.simulate_insert_failures,
            upload=args.upload,
            source=args.source,
            simulate_download_failures=args.simulate_download_failures,
            images=args.images,
            upload_only=args.upload_only,
            allow_empty=args.allow_empty,
        )
    except PipelineError as error:
        print(f"\nERREUR : {error}", file=sys.stderr)
        print("\nPipeline en échec.", file=sys.stderr)
        return 1
    print("\nPipeline terminée avec succès.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
