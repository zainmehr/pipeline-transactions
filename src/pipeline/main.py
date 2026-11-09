"""Point d'entrée de la pipeline.

Étape 1.2 : chargement des CSV d'un dossier puis affichage des résultats des traitements.
Ce module sera enrichi aux étapes suivantes (base de données, orchestration complète).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from pipeline.loading.csv_loader import InvalidCSVError, list_csv_files, load_csv
from pipeline.models import ProcessingResult, Transaction
from pipeline.processing.transformations import FLAG_THRESHOLD, process_transactions


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Charge et traite les fichiers CSV.")
    parser.add_argument("--input", type=Path, default=Path("data"), help="Dossier des CSV")
    args = parser.parse_args(argv)

    try:
        files = list_csv_files(args.input)
    except FileNotFoundError as error:
        print(f"ERREUR : {error}", file=sys.stderr)
        return 1
    if not files:
        print(f"ERREUR : aucun fichier CSV dans {args.input}", file=sys.stderr)
        return 1

    transactions: list[Transaction] = []
    for path in files:
        try:
            loaded = load_csv(path)
        except InvalidCSVError as error:
            print(f"ERREUR : {error}", file=sys.stderr)
            return 1
        print(f"{path.name} : {len(loaded)} transactions chargées")
        transactions.extend(loaded)

    print_report(process_transactions(transactions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
