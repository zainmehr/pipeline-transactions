"""Chargement des fichiers CSV (entrées/sorties uniquement).

Ce module se charge de lire les fichiers ; la validation est déléguée au module
`pipeline.validation` et les traitements au module `pipeline.processing`.
"""

from __future__ import annotations

import csv
from pathlib import Path

from pipeline.models import Transaction
from pipeline.validation import InvalidTransactionError, parse_transaction, validate_header


class InvalidCSVError(Exception):
    """Un fichier CSV ne peut pas être chargé ; contient la liste des erreurs détectées."""

    def __init__(self, path: Path, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        details = "\n".join(f"  - {error}" for error in errors)
        super().__init__(f"Fichier CSV invalide : {path.name} ({len(errors)} erreur(s))\n{details}")


def list_csv_files(directory: Path) -> list[Path]:
    """Retourne les fichiers .csv d'un dossier, triés par nom."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {directory}")
    return sorted(path for path in directory.glob("*.csv") if path.is_file())


def load_csv(path: Path) -> list[Transaction]:
    """Charge et valide toutes les transactions d'un fichier CSV.

    Lève InvalidCSVError en listant toutes les lignes invalides, avec leur numéro.
    """
    transactions: list[Transaction] = []
    errors: list[str] = []
    try:
        # utf-8-sig accepte aussi les fichiers avec BOM (exports Excel).
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            try:
                validate_header(reader.fieldnames)
            except InvalidTransactionError as error:
                raise InvalidCSVError(path, [str(error)]) from error

            for row in reader:
                try:
                    transactions.append(parse_transaction(row))
                except InvalidTransactionError as error:
                    errors.append(f"ligne {reader.line_num} : {error}")
    except UnicodeDecodeError as error:
        raise InvalidCSVError(path, [f"encodage invalide (UTF-8 attendu) : {error}"]) from error

    if errors:
        raise InvalidCSVError(path, errors)
    return transactions
