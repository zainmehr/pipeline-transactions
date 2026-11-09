"""Validation et conversion des lignes CSV en transactions.

Toutes les fonctions de ce module sont pures : elles ne lisent aucun fichier et
ne modifient pas leurs entrées. Les erreurs d'une ligne sont toutes collectées
avant d'être levées, pour afficher un diagnostic complet en une seule fois.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pipeline.generation.iban import is_valid_iban
from pipeline.models import CSV_COLUMNS, Transaction

CENTS = Decimal("0.01")


class InvalidTransactionError(ValueError):
    """Une ligne ou un en-tête CSV ne respecte pas le format attendu."""


def validate_header(fieldnames: Sequence[str] | None) -> None:
    """Vérifie que l'en-tête contient exactement les colonnes attendues."""
    if not fieldnames:
        raise InvalidTransactionError("fichier vide ou en-tête absent")

    errors = []
    missing = [col for col in CSV_COLUMNS if col not in fieldnames]
    unexpected = [col for col in fieldnames if col not in CSV_COLUMNS]
    if missing:
        errors.append(f"colonnes manquantes : {', '.join(missing)}")
    if unexpected:
        errors.append(f"colonnes inattendues : {', '.join(unexpected)}")
    if errors:
        raise InvalidTransactionError(" ; ".join(errors))


def _parse_datetime(value: str, errors: list[str]) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        errors.append(f"datetime_transaction invalide (format ISO 8601 attendu) : {value!r}")
        return None


def _parse_amount(value: str, errors: list[str]) -> Decimal | None:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        errors.append(f"montant invalide : {value!r}")
        return None
    if not amount.is_finite():
        errors.append(f"montant invalide : {value!r}")
        return None
    if amount <= 0:
        errors.append(f"montant doit être strictement positif : {value!r}")
        return None
    if amount.as_tuple().exponent < -2:
        errors.append(f"montant avec plus de 2 décimales : {value!r}")
        return None
    return amount.quantize(CENTS)


def _check_account(
    iban_field: str, iban: str, pays_field: str, pays: str, errors: list[str]
) -> None:
    if not (len(pays) == 2 and pays.isalpha() and pays.isupper()):
        errors.append(f"{pays_field} doit être un code pays ISO à 2 lettres : {pays!r}")
    if not is_valid_iban(iban):
        errors.append(f"{iban_field} invalide : {iban!r}")
    elif iban[:2] != pays:
        errors.append(f"{pays_field} {pays!r} incohérent avec {iban_field} {iban!r}")


def parse_transaction(row: Mapping[str | None, object]) -> Transaction:
    """Convertit une ligne CSV (dict de chaînes) en Transaction validée."""
    if row.get(None):
        raise InvalidTransactionError("la ligne contient plus de valeurs que de colonnes")

    values = {col: str(row.get(col) or "").strip() for col in CSV_COLUMNS}
    empty = [col for col, value in values.items() if not value]
    if empty:
        raise InvalidTransactionError(f"valeurs manquantes : {', '.join(empty)}")

    errors: list[str] = []
    date = _parse_datetime(values["datetime_transaction"], errors)
    amount = _parse_amount(values["montant"], errors)
    _check_account(
        "iban_origine", values["iban_origine"], "pays_source", values["pays_source"], errors
    )
    _check_account(
        "iban_destinataire",
        values["iban_destinataire"],
        "pays_destinataire",
        values["pays_destinataire"],
        errors,
    )
    if values["iban_origine"] == values["iban_destinataire"]:
        errors.append("iban_origine et iban_destinataire sont identiques")

    if errors or date is None or amount is None:
        raise InvalidTransactionError(" ; ".join(errors))

    return Transaction(
        datetime_transaction=date,
        iban_origine=values["iban_origine"],
        pays_source=values["pays_source"],
        banque_source=values["banque_source"],
        iban_destinataire=values["iban_destinataire"],
        pays_destinataire=values["pays_destinataire"],
        montant=amount,
    )
