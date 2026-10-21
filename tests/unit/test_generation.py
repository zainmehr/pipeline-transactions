import csv
from decimal import Decimal

import pytest

from pipeline.generation.accounts import ACCOUNTS
from pipeline.generation.generator import (
    CSV_COLUMNS,
    EDGE_CASE_AMOUNTS,
    MAX_TRANSACTIONS,
    MIN_TRANSACTIONS,
    generate_batch,
    generate_files,
)
from pipeline.generation.iban import build_iban, is_valid_iban

# --- IBAN -----------------------------------------------------------------


def test_build_iban_reproduit_un_exemple_officiel():
    assert build_iban("DE", "370400440532013000") == "DE89370400440532013000"


@pytest.mark.parametrize("iban", ["DE89370400440532013000", "GB82WEST12345698765432"])
def test_is_valid_iban_accepte_des_iban_valides(iban):
    assert is_valid_iban(iban)


@pytest.mark.parametrize(
    "iban", ["DE88370400440532013000", "DE89", "", "12ABCDEFGHIJKLMNOP", "FR76 3000-6000"]
)
def test_is_valid_iban_rejette_des_iban_invalides(iban):
    assert not is_valid_iban(iban)


def test_tous_les_comptes_du_referentiel_sont_valides():
    for account in ACCOUNTS:
        assert is_valid_iban(account.iban)
        assert account.iban.startswith(account.pays)


def test_le_referentiel_permet_de_distinguer_iban_et_banque():
    banques = [account.banque for account in ACCOUNTS]
    assert len(set(banques)) < len(banques)  # au moins une banque a plusieurs comptes


# --- Génération -----------------------------------------------------------


def test_generation_deterministe():
    assert generate_batch(seed=42, batch_index=3) == generate_batch(seed=42, batch_index=3)


def test_seeds_differentes_donnent_des_donnees_differentes():
    assert generate_batch(seed=1, batch_index=0) != generate_batch(seed=2, batch_index=0)


@pytest.mark.parametrize("batch_index", range(50))
def test_contraintes_de_chaque_lot(batch_index):
    transactions = generate_batch(seed=42, batch_index=batch_index)

    assert MIN_TRANSACTIONS <= len(transactions) <= MAX_TRANSACTIONS
    dates = [tx.datetime_transaction for tx in transactions]
    assert dates == sorted(dates)
    for tx in transactions:
        assert tx.iban_origine != tx.iban_destinataire
        assert tx.montant > 0
        assert tx.montant == tx.montant.quantize(Decimal("0.01"))


def test_premier_lot_contient_les_cas_limites_du_flag():
    montants = {tx.montant for tx in generate_batch(seed=42, batch_index=0)}
    assert set(EDGE_CASE_AMOUNTS) <= montants


# --- Écriture des fichiers ------------------------------------------------


def test_generate_files_ecrit_des_csv_conformes(tmp_path):
    paths = generate_files(tmp_path, nb_files=3, seed=42)

    assert [p.name for p in paths] == [
        "transactions_001.csv",
        "transactions_002.csv",
        "transactions_003.csv",
    ]
    for index, path in enumerate(paths):
        with path.open(encoding="utf-8") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
        assert tuple(reader.fieldnames) == CSV_COLUMNS
        expected = [tx.to_row() for tx in generate_batch(seed=42, batch_index=index)]
        assert rows == expected


def test_generate_files_refuse_un_nombre_de_fichiers_nul(tmp_path):
    with pytest.raises(ValueError):
        generate_files(tmp_path, nb_files=0)
