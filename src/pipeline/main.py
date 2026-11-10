"""Point d'entrée de la pipeline : orchestre toutes les étapes.

    [1/4] Génération des CSV            (option --generate N)
    [2/4] Préparation de la base        (option --save) : schéma + fichiers déjà traités
    [3/4] Chargement et traitement      : validation de tous les nouveaux fichiers
    [4/4] Insertion en base             (option --save)

Un fichier déjà traité (même contenu, donc même hash) est ignoré : la pipeline peut être
relancée autant de fois que nécessaire sans jamais compter une transaction deux fois.
Si un seul fichier est invalide, rien n'est inséré et la commande retourne le code 1.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from pipeline.generation.generator import DEFAULT_SEED, generate_files
from pipeline.loading.csv_loader import (
    InvalidCSVError,
    compute_file_hash,
    list_csv_files,
    load_csv,
)
from pipeline.models import ProcessingResult, Transaction
from pipeline.processing.transformations import FLAG_THRESHOLD, process_transactions
from pipeline.storage.database import (
    DatabaseConfig,
    DuplicateFileError,
    apply_schema,
    connect,
    fetch_processed_hashes,
    insert_processed_file,
)


class PipelineError(Exception):
    """Erreur qui arrête la pipeline ; son message est affiché à l'utilisateur."""


@dataclass(frozen=True)
class CsvFile:
    path: Path
    file_hash: str


@dataclass(frozen=True)
class LoadedFile:
    file: CsvFile
    transactions: list[Transaction]


# --- Affichage ---------------------------------------------------------------


def step(number: int, title: str) -> None:
    print(f"\n[{number}/4] {title}")


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


def discover_files(directory: Path) -> list[CsvFile]:
    try:
        paths = list_csv_files(directory)
    except FileNotFoundError as error:
        raise PipelineError(str(error)) from error
    if not paths:
        raise PipelineError(f"aucun fichier CSV dans {directory}")
    return [CsvFile(path, compute_file_hash(path)) for path in paths]


def exclude_processed(files: Sequence[CsvFile], processed_hashes: set[str]) -> list[CsvFile]:
    new_files = []
    for file in files:
        if file.file_hash in processed_hashes:
            print(f"  {file.path.name} : déjà traité, ignoré")
        else:
            new_files.append(file)
    print(f"  {len(new_files)} nouveau(x) fichier(s) à traiter sur {len(files)}")
    return new_files


def load_files(files: Sequence[CsvFile]) -> list[LoadedFile]:
    """Charge et valide tous les fichiers avant toute insertion."""
    loaded = []
    for file in files:
        try:
            transactions = load_csv(file.path)
        except InvalidCSVError as error:
            raise PipelineError(f"{error}\nAucune donnée n'a été insérée.") from error
        print(f"  {file.path.name} : {len(transactions)} transactions chargées")
        loaded.append(LoadedFile(file, transactions))
    return loaded


def insert_files(conn: psycopg.Connection, files: Sequence[LoadedFile]) -> None:
    for loaded in files:
        name = loaded.file.path.name
        try:
            file_id = insert_processed_file(
                conn, name, loaded.file.file_hash, process_transactions(loaded.transactions)
            )
        except DuplicateFileError:
            # Cas rare : le fichier a été inséré entre la vérification et l'insertion.
            print(f"  {name} : déjà traité, ignoré")
            continue
        print(f"  {name} inséré (id={file_id}, {len(loaded.transactions)} transactions)")


def run(input_dir: Path, generate: int = 0, seed: int = DEFAULT_SEED, save: bool = False) -> None:
    """Exécute la pipeline. Lève PipelineError en cas d'échec."""
    step(1, "Génération des CSV")
    if generate:
        for path in generate_files(input_dir, generate, seed):
            print(f"  {path.name} généré")
    else:
        print("  ignorée (utiliser --generate N)")

    files = discover_files(input_dir)

    if not save:
        step(2, "Préparation de la base")
        print("  ignorée (utiliser --save)")
        step(3, "Chargement et traitement")
        loaded = load_files(files)
        print_report(process_transactions([tx for lf in loaded for tx in lf.transactions]))
        step(4, "Insertion en base")
        print("  ignorée (utiliser --save)")
        return

    config = DatabaseConfig.from_env()
    try:
        with connect(config) as conn:
            step(2, "Préparation de la base")
            apply_schema(conn)
            print(f"  base {config.describe()} prête")
            new_files = exclude_processed(files, fetch_processed_hashes(conn))

            step(3, "Chargement et traitement")
            loaded = load_files(new_files)
            if loaded:
                print_report(process_transactions([tx for lf in loaded for tx in lf.transactions]))
            else:
                print("  aucun nouveau fichier")

            step(4, "Insertion en base")
            insert_files(conn, loaded)
    except psycopg.OperationalError as error:
        raise PipelineError(
            f"base de données inaccessible ({config.describe()}) : {error}"
        ) from error


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()  # charge le fichier .env s'il existe (sans écraser l'environnement)
    parser = argparse.ArgumentParser(description="Pipeline de transactions financières.")
    parser.add_argument("--input", type=Path, default=Path("data"), help="Dossier des CSV")
    parser.add_argument(
        "--generate", type=int, default=0, metavar="N", help="Génère N fichiers avant traitement"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Graine de génération")
    parser.add_argument("--save", action="store_true", help="Insère les résultats en base")
    args = parser.parse_args(argv)

    try:
        run(args.input, args.generate, args.seed, args.save)
    except PipelineError as error:
        print(f"\nERREUR : {error}", file=sys.stderr)
        print("\nPipeline en échec.", file=sys.stderr)
        return 1
    print("\nPipeline terminée avec succès.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
