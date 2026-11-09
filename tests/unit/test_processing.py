import copy
from decimal import Decimal

import pytest

from pipeline.generation.generator import generate_batch
from pipeline.processing.transformations import (
    flag_transactions,
    is_high_amount,
    process_transactions,
    sum_received_by_iban,
    sum_sent_by_bank,
    sum_sent_by_iban,
)
from tests.factories import BNP_1, BNP_2, DEUTSCHE, make_transaction

# Jeu de données construit à la main, avec des résultats calculables de tête :
#   BNP_1    -> DEUTSCHE   100.00
#   BNP_1    -> BNP_2      250.50
#   BNP_2    -> DEUTSCHE  6000.00   (flaggée)
#   DEUTSCHE -> BNP_1     5000.00   (non flaggée : 5000 n'est pas > 5000)
TRANSACTIONS = [
    make_transaction(BNP_1, DEUTSCHE, "100.00"),
    make_transaction(BNP_1, BNP_2, "250.50"),
    make_transaction(BNP_2, DEUTSCHE, "6000.00"),
    make_transaction(DEUTSCHE, BNP_1, "5000.00"),
]


def test_somme_envoyee_par_iban():
    assert sum_sent_by_iban(TRANSACTIONS) == {
        BNP_1.iban: Decimal("350.50"),
        BNP_2.iban: Decimal("6000.00"),
        DEUTSCHE.iban: Decimal("5000.00"),
    }


def test_somme_envoyee_par_banque():
    assert sum_sent_by_bank(TRANSACTIONS) == {
        "BNP Paribas": Decimal("6350.50"),
        "Deutsche Bank": Decimal("5000.00"),
    }


def test_somme_recue_par_iban():
    assert sum_received_by_iban(TRANSACTIONS) == {
        DEUTSCHE.iban: Decimal("6100.00"),
        BNP_2.iban: Decimal("250.50"),
        BNP_1.iban: Decimal("5000.00"),
    }


def test_flags():
    assert [ft.is_flagged for ft in flag_transactions(TRANSACTIONS)] == [
        False,
        False,
        True,
        False,
    ]


@pytest.mark.parametrize(
    ("montant", "attendu"),
    [("0.01", False), ("4999.99", False), ("5000.00", False), ("5000.01", True), ("20000", True)],
)
def test_seuil_du_flag_strictement_superieur(montant, attendu):
    assert is_high_amount(make_transaction(montant=montant)) is attendu


def test_seuil_personnalisable():
    assert is_high_amount(make_transaction(montant="150"), threshold=Decimal("100"))


def test_entree_vide():
    result = process_transactions([])
    assert result.transactions == ()
    assert result.sent_by_iban == result.sent_by_bank == result.received_by_iban == {}


def test_process_transactions_regroupe_tous_les_traitements():
    result = process_transactions(TRANSACTIONS)
    assert result.sent_by_iban == sum_sent_by_iban(TRANSACTIONS)
    assert result.sent_by_bank == sum_sent_by_bank(TRANSACTIONS)
    assert result.received_by_iban == sum_received_by_iban(TRANSACTIONS)
    assert [ft.transaction for ft in result.transactions] == TRANSACTIONS


def test_cas_limites_du_generateur_correctement_flagges():
    flags = {
        ft.transaction.montant: ft.is_flagged for ft in flag_transactions(generate_batch(42, 0))
    }
    assert flags[Decimal("4999.99")] is False
    assert flags[Decimal("5000.00")] is False
    assert flags[Decimal("5000.01")] is True


# --- Pureté ----------------------------------------------------------------

TRAITEMENTS = [
    sum_sent_by_iban,
    sum_sent_by_bank,
    sum_received_by_iban,
    flag_transactions,
    process_transactions,
]


@pytest.mark.parametrize("traitement", TRAITEMENTS)
def test_ne_modifie_pas_les_donnees_en_entree(traitement):
    entree = list(TRANSACTIONS)
    copie = copy.deepcopy(entree)
    traitement(entree)
    assert entree == copie


@pytest.mark.parametrize("traitement", TRAITEMENTS)
def test_meme_entree_meme_resultat(traitement):
    assert traitement(TRANSACTIONS) == traitement(TRANSACTIONS)


@pytest.mark.parametrize("traitement", [sum_sent_by_iban, sum_sent_by_bank, sum_received_by_iban])
def test_agregations_independantes_de_l_ordre(traitement):
    assert traitement(TRANSACTIONS) == traitement(list(reversed(TRANSACTIONS)))
