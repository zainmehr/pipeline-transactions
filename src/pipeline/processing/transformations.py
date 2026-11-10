"""Traitements des transactions.

Toutes les fonctions sont pures :
- elles ne modifient pas les données reçues (les transactions sont immuables et les
  listes d'entrée ne sont jamais modifiées) ;
- elles ne dépendent d'aucun état externe (ni fichier, ni base, ni variable globale mutable) ;
- elles produisent toujours le même résultat pour les mêmes entrées.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

from pipeline.models import FlaggedTransaction, ProcessingResult, Transaction

FLAG_THRESHOLD = Decimal("5000")
ZERO = Decimal("0.00")


def _sum_by(
    transactions: Sequence[Transaction], key: Callable[[Transaction], str]
) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for transaction in transactions:
        group = key(transaction)
        totals[group] = totals.get(group, ZERO) + transaction.montant
    return dict(sorted(totals.items()))


def sum_sent_by_iban(transactions: Sequence[Transaction]) -> dict[str, Decimal]:
    """Somme des montants envoyés par iban_origine."""
    return _sum_by(transactions, lambda tx: tx.iban_origine)


def sum_sent_by_bank(transactions: Sequence[Transaction]) -> dict[str, Decimal]:
    """Somme des montants envoyés par banque_source."""
    return _sum_by(transactions, lambda tx: tx.banque_source)


def sum_received_by_iban(transactions: Sequence[Transaction]) -> dict[str, Decimal]:
    """Somme des montants reçus par iban_destinataire."""
    return _sum_by(transactions, lambda tx: tx.iban_destinataire)


def is_high_amount(transaction: Transaction, threshold: Decimal = FLAG_THRESHOLD) -> bool:
    """Vrai si le montant est strictement supérieur au seuil."""
    return transaction.montant >= threshold


def flag_transactions(
    transactions: Sequence[Transaction], threshold: Decimal = FLAG_THRESHOLD
) -> list[FlaggedTransaction]:
    """Associe à chaque transaction un flag « montant supérieur au seuil »."""
    return [FlaggedTransaction(tx, is_high_amount(tx, threshold)) for tx in transactions]


def process_transactions(transactions: Sequence[Transaction]) -> ProcessingResult:
    """Applique l'ensemble des traitements demandés."""
    return ProcessingResult(
        transactions=tuple(flag_transactions(transactions)),
        sent_by_iban=sum_sent_by_iban(transactions),
        sent_by_bank=sum_sent_by_bank(transactions),
        received_by_iban=sum_received_by_iban(transactions),
    )
