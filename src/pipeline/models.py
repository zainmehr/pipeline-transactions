"""Modèles de données partagés par toutes les étapes de la pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

CSV_COLUMNS: tuple[str, ...] = (
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
)


@dataclass(frozen=True)
class Transaction:
    """Transaction financière. `frozen=True` la rend immuable."""

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


@dataclass(frozen=True)
class FlaggedTransaction:
    """Transaction accompagnée de son flag « montant élevé »."""

    transaction: Transaction
    is_flagged: bool


@dataclass(frozen=True)
class ProcessingResult:
    """Ensemble des résultats produits par les traitements."""

    transactions: tuple[FlaggedTransaction, ...]
    sent_by_iban: Mapping[str, Decimal]
    sent_by_bank: Mapping[str, Decimal]
    received_by_iban: Mapping[str, Decimal]
