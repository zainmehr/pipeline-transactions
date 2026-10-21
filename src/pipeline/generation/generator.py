"""Génération déterministe de fichiers CSV de transactions financières.

Les données sont pseudo-aléatoires mais reproductibles : une même graine (seed)
produit toujours exactement les mêmes fichiers. La génération des transactions
(pure) est séparée de l'écriture sur disque (I/O).
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pipeline.generation.accounts import ACCOUNTS

CSV_COLUMNS: tuple[str, ...] = (
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
)

MIN_TRANSACTIONS = 5
MAX_TRANSACTIONS = 10
DEFAULT_SEED = 42
DEFAULT_NB_FILES = 5
BASE_DATETIME = datetime(2026, 1, 1, 8, 0, 0)

# Cas limites autour du seuil de flag (5000) injectés dans le premier fichier.
EDGE_CASE_AMOUNTS: tuple[Decimal, ...] = (
    Decimal("4999.99"),
    Decimal("5000.00"),
    Decimal("5000.01"),
)
HIGH_AMOUNT_PROBABILITY = 0.25
CENTS = Decimal("0.01")


@dataclass(frozen=True)
class Transaction:
    datetime_transaction: datetime
    iban_origine: str
    pays_source: str
    banque_source: str
    iban_destinataire: str
    pays_destinataire: str
    montant: Decimal

    def to_row(self) -> dict[str, str]:
        """Sérialise la transaction en ligne CSV."""
        return {
            "datetime_transaction": self.datetime_transaction.isoformat(),
            "iban_origine": self.iban_origine,
            "pays_source": self.pays_source,
            "banque_source": self.banque_source,
            "iban_destinataire": self.iban_destinataire,
            "pays_destinataire": self.pays_destinataire,
            "montant": f"{self.montant:.2f}",
        }


def _random_amount(rng: random.Random) -> Decimal:
    """Tire un montant : majoritairement sous 5000, parfois au-dessus."""
    if rng.random() < HIGH_AMOUNT_PROBABILITY:
        cents = rng.randint(500_001, 2_000_000)  # 5000.01 € à 20 000 €
    else:
        cents = rng.randint(1_000, 499_999)  # 10 € à 4999.99 €
    return (Decimal(cents) / 100).quantize(CENTS)


def generate_batch(seed: int, batch_index: int) -> list[Transaction]:
    """Génère les transactions d'un fichier. Même (seed, batch_index) => même résultat."""
    rng = random.Random(seed * 10_000 + batch_index)
    nb_transactions = rng.randint(MIN_TRANSACTIONS, MAX_TRANSACTIONS)
    current = BASE_DATETIME + timedelta(days=batch_index)

    transactions: list[Transaction] = []
    for _ in range(nb_transactions):
        origin, destination = rng.sample(ACCOUNTS, 2)  # garantit origine != destination
        current += timedelta(minutes=rng.randint(1, 90), seconds=rng.randint(0, 59))
        transactions.append(
            Transaction(
                datetime_transaction=current,
                iban_origine=origin.iban,
                pays_source=origin.pays,
                banque_source=origin.banque,
                iban_destinataire=destination.iban,
                pays_destinataire=destination.pays,
                montant=_random_amount(rng),
            )
        )

    if batch_index == 0:
        for i, amount in enumerate(EDGE_CASE_AMOUNTS):
            transactions[i] = replace(transactions[i], montant=amount)

    return transactions


def write_csv(transactions: list[Transaction], path: Path) -> Path:
    """Écrit une liste de transactions dans un fichier CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(tx.to_row() for tx in transactions)
    return path


def generate_files(
    output_dir: Path, nb_files: int = DEFAULT_NB_FILES, seed: int = DEFAULT_SEED
) -> list[Path]:
    """Génère `nb_files` fichiers CSV dans `output_dir` et retourne leurs chemins."""
    if nb_files < 1:
        raise ValueError("nb_files doit être supérieur ou égal à 1")
    return [
        write_csv(generate_batch(seed, i), output_dir / f"transactions_{i + 1:03d}.csv")
        for i in range(nb_files)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère des fichiers CSV de transactions.")
    parser.add_argument("--output", type=Path, default=Path("data"), help="Dossier de sortie")
    parser.add_argument("--files", type=int, default=DEFAULT_NB_FILES, help="Nombre de fichiers")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Graine aléatoire")
    args = parser.parse_args()

    paths = generate_files(args.output, args.files, args.seed)
    for path in paths:
        print(f"Fichier généré : {path}")


if __name__ == "__main__":
    main()
