"""Chargement des fichiers CSV.

Le décodage et la validation d'un contenu CSV (`parse_csv`) ne dépendent pas de l'endroit
d'où vient le fichier : disque local ou stockage S3. Seules les fonctions de lecture
(`load_csv`, `list_csv_files`) accèdent au système de fichiers.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from pipeline.models import Transaction
from pipeline.validation import InvalidTransactionError, parse_transaction, validate_header


class InvalidCSVError(Exception):
    """Un fichier CSV ne peut pas être chargé ; contient la liste des erreurs détectées."""

    def __init__(self, path: Path | str, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        name = Path(str(path)).name
        details = "\n".join(f"  - {error}" for error in errors)
        super().__init__(f"Fichier CSV invalide : {name} ({len(errors)} erreur(s))\n{details}")


def compute_hash(content: bytes) -> str:
    """Empreinte SHA-256 d'un contenu : identifie un fichier indépendamment de son nom."""
    return hashlib.sha256(content).hexdigest()


def compute_file_hash(path: Path) -> str:
    """Empreinte SHA-256 du contenu d'un fichier local."""
    return compute_hash(path.read_bytes())


def list_csv_files(directory: Path) -> list[Path]:
    """Retourne les fichiers .csv d'un dossier, triés par nom."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {directory}")
    return sorted(path for path in directory.glob("*.csv") if path.is_file())


def parse_csv(content: bytes, source: Path | str) -> list[Transaction]:
    """Décode et valide toutes les transactions d'un contenu CSV.

    `source` sert uniquement aux messages d'erreur. Lève InvalidCSVError en listant toutes
    les lignes invalides, avec leur numéro.
    """
    try:
        # utf-8-sig accepte aussi les fichiers avec BOM (exports Excel).
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InvalidCSVError(source, [f"encodage invalide (UTF-8 attendu) : {error}"]) from error

    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        validate_header(reader.fieldnames)
    except InvalidTransactionError as error:
        raise InvalidCSVError(source, [str(error)]) from error

    transactions: list[Transaction] = []
    errors: list[str] = []
    for row in reader:
        try:
            transactions.append(parse_transaction(row))
        except InvalidTransactionError as error:
            errors.append(f"ligne {reader.line_num} : {error}")

    if errors:
        raise InvalidCSVError(source, errors)
    return transactions


def load_csv(path: Path) -> list[Transaction]:
    """Lit un fichier CSV local, puis le décode et le valide."""
    return parse_csv(path.read_bytes(), path)
