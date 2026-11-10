"""Récupération des fichiers CSV à traiter, depuis le disque local ou le stockage S3.

Chaque fichier récupéré est un `SourceFile` : son nom, son origine (chemin ou URI S3), son
empreinte SHA-256 et son contenu. La suite de la pipeline (chargement, traitement,
insertion) ne dépend plus de l'endroit d'où vient le fichier.

Récupération depuis S3 :

    S3 ──liste──> pour chaque objet :
                    empreinte déjà traitée ? ──oui──> ignoré, sans téléchargement
                    sinon téléchargement (avec retry) ──> contrôle d'intégrité ──> SourceFile
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from pipeline.loading.csv_loader import compute_hash, list_csv_files
from pipeline.retry import RetryPolicy, retry
from pipeline.storage.s3 import (
    TRANSIENT_S3_ERRORS,
    TransientS3Error,
    download_object,
    list_keys,
    object_hash,
)


@dataclass(frozen=True)
class SourceFile:
    name: str  # nom court, pour l'affichage
    origin: str  # chemin local ou URI s3://, enregistré en base pour la traçabilité
    file_hash: str
    content: bytes


def read_local_files(directory: Path) -> list[SourceFile]:
    """Lit les fichiers .csv d'un dossier local. Lève FileNotFoundError si le dossier
    n'existe pas."""
    return [
        SourceFile(path.name, path.name, compute_hash(content), content)
        for path in list_csv_files(directory)
        for content in [path.read_bytes()]
    ]


def exclude_processed(
    files: Iterable[SourceFile],
    processed_hashes: set[str],
    log: Callable[[str], None] = print,
) -> list[SourceFile]:
    """Retire les fichiers déjà traités, et les doublons de contenu dans le lot."""
    files = list(files)
    seen = set(processed_hashes)
    new_files = []
    for file in files:
        if file.file_hash in seen:
            log(f"  {file.name} : déjà traité, ignoré")
            continue
        seen.add(file.file_hash)
        new_files.append(file)
    log(f"  {len(new_files)} nouveau(x) fichier(s) à traiter sur {len(files)}")
    return new_files


def fetch_s3_files(
    client,
    bucket: str,
    processed_hashes: set[str],
    policy: RetryPolicy,
    inject_failure: Callable[[str], None] = lambda name: None,
    log: Callable[[str], None] = print,
) -> tuple[list[SourceFile], int]:
    """Récupère depuis le bucket les fichiers CSV non encore traités.

    Retourne les nouveaux fichiers et le nombre total de fichiers présents dans le bucket.
    Chaque accès au stockage est réessayé en cas d'erreur temporaire ; au-delà du nombre
    maximal de tentatives, RetryError est levée et rien n'a été traité.
    `inject_failure(nom)` est appelée avant chaque téléchargement : elle sert à simuler
    une panne (option --simulate-download-failures).
    """

    def call(func, description):
        return retry(func, policy, TRANSIENT_S3_ERRORS, description, log=log)

    keys = call(
        lambda: [key for key in list_keys(client, bucket) if key.endswith(".csv")],
        "liste des fichiers du bucket",
    )
    # Traitement dans l'ordre des noms de fichiers (la clé commence par l'empreinte).
    keys.sort(key=lambda key: (key.rsplit("/", 1)[-1], key))
    seen = set(processed_hashes)
    new_files: list[SourceFile] = []

    for key in keys:
        name = key.rsplit("/", 1)[-1]
        origin = f"s3://{bucket}/{key}"

        # L'empreinte enregistrée à l'envoi évite de télécharger un fichier déjà traité.
        declared_hash = call(
            lambda key=key: object_hash(client, bucket, key), f"métadonnées de {name}"
        )
        if declared_hash in seen:
            log(f"  {name} : déjà traité, ignoré")
            continue

        def attempt(key=key, name=name, declared_hash=declared_hash) -> tuple[bytes, str]:
            inject_failure(name)
            content = download_object(client, bucket, key)
            actual_hash = compute_hash(content)
            if declared_hash and actual_hash != declared_hash:
                raise TransientS3Error(
                    f"contenu reçu altéré pour {name} : empreinte différente de celle enregistrée"
                )
            return content, actual_hash

        content, file_hash = call(attempt, f"récupération de {name}")
        if file_hash in seen:
            log(f"  {name} : déjà traité, ignoré")
            continue
        seen.add(file_hash)
        log(f"  {name} récupéré depuis {origin} ({len(content)} octets)")
        new_files.append(SourceFile(name, origin, file_hash, content))

    log(f"  {len(new_files)} nouveau(x) fichier(s) à traiter sur {len(keys)}")
    return new_files, len(keys)
