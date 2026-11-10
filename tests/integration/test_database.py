from decimal import Decimal

import psycopg
import pytest

from pipeline.generation.generator import generate_batch
from pipeline.processing.transformations import process_transactions
from pipeline.storage.database import (
    DuplicateFileError,
    apply_schema,
    fetch_processed_hashes,
    insert_processed_file,
)
from tests.factories import BNP_1, BNP_2, DEUTSCHE, make_transaction

pytestmark = pytest.mark.integration

HASH_A = "a" * 64
HASH_B = "b" * 64


def count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def totals(db, query):
    return dict(db.execute(query).fetchall())


def test_fichiers_deja_traites(db):
    assert fetch_processed_hashes(db) == set()
    insert_processed_file(db, "f1.csv", HASH_A, process_transactions(generate_batch(42, 0)))
    insert_processed_file(db, "f2.csv", HASH_B, process_transactions(generate_batch(42, 1)))
    assert fetch_processed_hashes(db) == {HASH_A, HASH_B}


def test_schema_rejouable(db):
    apply_schema(db)
    apply_schema(db)


def test_insertion_d_un_fichier(db):
    transactions = [
        make_transaction(BNP_1, DEUTSCHE, "100.00"),
        make_transaction(BNP_2, DEUTSCHE, "6000.00"),
    ]
    file_id = insert_processed_file(db, "f1.csv", HASH_A, process_transactions(transactions))

    assert db.execute(
        "SELECT id, file_name, file_hash, nb_transactions FROM processed_files"
    ).fetchall() == [(file_id, "f1.csv", HASH_A, 2)]

    rows = db.execute(
        """
        SELECT t.iban_origine, o.pays, b.name, t.iban_destinataire, d.pays,
               t.montant, t.flag_montant_eleve, t.file_id
        FROM transactions t
        JOIN accounts o ON o.iban = t.iban_origine
        JOIN accounts d ON d.iban = t.iban_destinataire
        JOIN banks b ON b.id = t.bank_source_id
        ORDER BY t.id
        """
    ).fetchall()
    assert rows == [
        (BNP_1.iban, "FR", "BNP Paribas", DEUTSCHE.iban, "DE", Decimal("100.00"), False, file_id),
        (BNP_2.iban, "FR", "BNP Paribas", DEUTSCHE.iban, "DE", Decimal("6000.00"), True, file_id),
    ]
    assert count(db, "accounts") == 3
    assert count(db, "banks") == 1


def test_sommes_enregistrees(db):
    result = process_transactions(generate_batch(seed=42, batch_index=0))
    insert_processed_file(db, "f1.csv", HASH_A, result)

    assert totals(db, "SELECT iban, total FROM total_sent_by_iban") == result.sent_by_iban
    assert totals(db, "SELECT iban, total FROM total_received_by_iban") == result.received_by_iban
    assert (
        totals(
            db,
            "SELECT b.name, t.total FROM total_sent_by_bank t JOIN banks b ON b.id = t.bank_id",
        )
        == result.sent_by_bank
    )


def test_sommes_cumulees_sur_plusieurs_fichiers(db):
    lot_1, lot_2 = generate_batch(42, 0), generate_batch(42, 1)
    insert_processed_file(db, "f1.csv", HASH_A, process_transactions(lot_1))
    insert_processed_file(db, "f2.csv", HASH_B, process_transactions(lot_2))

    attendu = process_transactions(lot_1 + lot_2)
    assert totals(db, "SELECT iban, total FROM total_sent_by_iban") == attendu.sent_by_iban
    assert totals(db, "SELECT iban, total FROM total_received_by_iban") == attendu.received_by_iban
    assert count(db, "transactions") == len(lot_1) + len(lot_2)


def test_tables_de_sommes_coherentes_avec_les_vues(db):
    for index in range(5):
        result = process_transactions(generate_batch(42, index))
        insert_processed_file(db, f"f{index}.csv", str(index) * 64, result)

    for table, view in [
        ("total_sent_by_iban", "v_total_sent_by_iban"),
        ("total_received_by_iban", "v_total_received_by_iban"),
    ]:
        assert totals(db, f"SELECT iban, total FROM {table}") == totals(
            db, f"SELECT iban, total FROM {view}"
        )
    assert totals(
        db, "SELECT b.name, t.total FROM total_sent_by_bank t JOIN banks b ON b.id = t.bank_id"
    ) == totals(db, "SELECT bank, total FROM v_total_sent_by_bank")


def test_flags_enregistres(db):
    insert_processed_file(db, "f1.csv", HASH_A, process_transactions(generate_batch(42, 0)))
    flags = totals(db, "SELECT montant, flag_montant_eleve FROM transactions")
    assert flags[Decimal("4999.99")] is False
    assert flags[Decimal("5000.00")] is False
    assert flags[Decimal("5000.01")] is True


def test_fichier_deja_insere_refuse_sans_rien_modifier(db):
    result = process_transactions(generate_batch(42, 0))
    insert_processed_file(db, "f1.csv", HASH_A, result)
    avant = totals(db, "SELECT iban, total FROM total_sent_by_iban")

    with pytest.raises(DuplicateFileError, match="copie.csv"):
        insert_processed_file(db, "copie.csv", HASH_A, result)

    assert count(db, "processed_files") == 1
    assert count(db, "transactions") == len(result.transactions)
    assert totals(db, "SELECT iban, total FROM total_sent_by_iban") == avant


def test_insertion_atomique_en_cas_d_erreur(db):
    # Une transaction au montant négatif contourne la validation Python :
    # la contrainte CHECK de la base la rejette et rien ne doit être écrit.
    transactions = [make_transaction(montant="100.00"), make_transaction(montant="-1.00")]
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_processed_file(db, "f1.csv", HASH_A, process_transactions(transactions))

    for table in ["processed_files", "transactions", "accounts", "banks", "total_sent_by_iban"]:
        assert count(db, table) == 0


def test_fichier_sans_transaction(db):
    insert_processed_file(db, "vide.csv", HASH_A, process_transactions([]))
    assert db.execute("SELECT nb_transactions FROM processed_files").fetchone() == (0,)
    assert count(db, "transactions") == 0
