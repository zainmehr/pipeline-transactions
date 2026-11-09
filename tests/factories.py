"""Fabriques de données de test."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from pipeline.generation.accounts import ACCOUNTS
from pipeline.models import Transaction

# Comptes nommés pour rendre les tests lisibles.
BNP_1 = ACCOUNTS[0]  # BNP Paribas
BNP_2 = ACCOUNTS[1]  # BNP Paribas (2e compte de la même banque)
DEUTSCHE = ACCOUNTS[3]  # Deutsche Bank


def make_transaction(origin=BNP_1, destination=DEUTSCHE, montant="100.00", **overrides):
    transaction = Transaction(
        datetime_transaction=datetime(2026, 1, 1, 12, 0, 0),
        iban_origine=origin.iban,
        pays_source=origin.pays,
        banque_source=origin.banque,
        iban_destinataire=destination.iban,
        pays_destinataire=destination.pays,
        montant=Decimal(montant),
    )
    return replace(transaction, **overrides)
