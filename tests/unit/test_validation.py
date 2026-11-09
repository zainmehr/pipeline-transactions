from decimal import Decimal

import pytest

from pipeline.models import CSV_COLUMNS
from pipeline.validation import InvalidTransactionError, parse_transaction, validate_header
from tests.factories import BNP_1, make_transaction


def valid_row(**overrides):
    row = make_transaction().to_row()
    row.update(overrides)
    return row


def test_ligne_valide_convertie_en_transaction():
    transaction = make_transaction(montant="1234.56")
    assert parse_transaction(transaction.to_row()) == transaction


def test_montant_normalise_a_deux_decimales():
    assert parse_transaction(valid_row(montant="100")).montant == Decimal("100.00")


def test_espaces_superflus_ignores():
    assert parse_transaction(valid_row(montant="  42.00 ")).montant == Decimal("42.00")


@pytest.mark.parametrize(
    ("champ", "valeur", "message"),
    [
        ("montant", "abc", "montant invalide"),
        ("montant", "NaN", "montant invalide"),
        ("montant", "-5.00", "strictement positif"),
        ("montant", "0", "strictement positif"),
        ("montant", "10.123", "plus de 2 décimales"),
        ("datetime_transaction", "31/01/2026 10:00", "datetime_transaction invalide"),
        ("iban_origine", "FR0000000000000000000000000", "iban_origine invalide"),
        ("iban_destinataire", "pas-un-iban", "iban_destinataire invalide"),
        ("pays_source", "DE", "incohérent"),
        ("pays_destinataire", "France", "code pays ISO"),
        ("banque_source", "", "valeurs manquantes"),
    ],
)
def test_lignes_invalides_rejetees(champ, valeur, message):
    row = valid_row(**{champ: valeur})
    with pytest.raises(InvalidTransactionError, match=message):
        parse_transaction(row)


def test_origine_et_destinataire_identiques_rejetes():
    row = valid_row(iban_destinataire=BNP_1.iban, pays_destinataire=BNP_1.pays)
    with pytest.raises(InvalidTransactionError, match="identiques"):
        parse_transaction(row)


def test_toutes_les_erreurs_d_une_ligne_sont_signalees():
    row = valid_row(montant="-1", datetime_transaction="hier", pays_destinataire="FR")
    with pytest.raises(InvalidTransactionError) as excinfo:
        parse_transaction(row)
    message = str(excinfo.value)
    assert "montant" in message
    assert "datetime_transaction" in message
    assert "pays_destinataire" in message


def test_ligne_avec_trop_de_valeurs():
    row = valid_row()
    row[None] = ["valeur en trop"]
    with pytest.raises(InvalidTransactionError, match="plus de valeurs"):
        parse_transaction(row)


def test_parse_transaction_ne_modifie_pas_la_ligne():
    row = valid_row(montant=" 10 ")
    copie = dict(row)
    parse_transaction(row)
    assert row == copie


# --- En-tête ---------------------------------------------------------------


def test_entete_valide():
    validate_header(list(CSV_COLUMNS))
    validate_header(list(reversed(CSV_COLUMNS)))  # l'ordre des colonnes n'importe pas


@pytest.mark.parametrize(
    ("entete", "message"),
    [
        (None, "en-tête absent"),
        ([], "en-tête absent"),
        ([c for c in CSV_COLUMNS if c != "montant"], "colonnes manquantes : montant"),
        ([*CSV_COLUMNS, "commentaire"], "colonnes inattendues : commentaire"),
    ],
)
def test_entete_invalide(entete, message):
    with pytest.raises(InvalidTransactionError, match=message):
        validate_header(entete)
