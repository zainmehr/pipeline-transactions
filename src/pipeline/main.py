"""Point d'entrée de la pipeline.

Charge les CSV d'un dossier, applique les traitements, affiche un rapport et,
avec l'option --save, insère les résultats dans PostgreSQL.
Ce module sera enrichi à l'étape 1.4 (orchestration complète).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

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
    insert_processed_file,
)


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


def save_to_database(files: Sequence[tuple[Path, list[Transaction]]]) -> int:
    """Insère chaque fichier et ses résultats en base. Retourne le code de sortie."""
    config = DatabaseConfig.from_env()
    try:
        with connect(config) as conn:
            apply_schema(conn)
            print(f"\nBase de données : {config.describe()}")
            for path, transactions in files:
                file_id = insert_processed_file(
                    conn, path.name, compute_file_hash(path), process_transactions(transactions)
                )
                print(f"  {path.name} inséré (id={file_id}, {len(transactions)} transactions)")
    except DuplicateFileError as error:
        print(f"ERREUR : {error}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as error:
        print(
            f"ERREUR : base de données inaccessible ({config.describe()}) : {error}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()  # charge le fichier .env s'il existe (sans écraser l'environnement)
    parser = argparse.ArgumentParser(description="Charge et traite les fichiers CSV.")
    parser.add_argument("--input", type=Path, default=Path("data"), help="Dossier des CSV")
    parser.add_argument("--save", action="store_true", help="Insère les résultats en base")
    args = parser.parse_args(argv)

    try:
        paths = list_csv_files(args.input)
    except FileNotFoundError as error:
        print(f"ERREUR : {error}", file=sys.stderr)
        return 1
    if not paths:
        print(f"ERREUR : aucun fichier CSV dans {args.input}", file=sys.stderr)
        return 1

    files: list[tuple[Path, list[Transaction]]] = []
    for path in paths:
        try:
            transactions = load_csv(path)
        except InvalidCSVError as error:
            print(f"ERREUR : {error}", file=sys.stderr)
            return 1
        print(f"{path.name} : {len(transactions)} transactions chargées")
        files.append((path, transactions))

    print_report(process_transactions([tx for _, txs in files for tx in txs]))

    if args.save:
        return save_to_database(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
