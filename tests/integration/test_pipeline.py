"""Tests de bout en bout : la pipeline complète, exécutée comme en ligne de commande,
sur une vraie base PostgreSQL."""

import csv
from decimal import Decimal

import pytest

from pipeline.generation.generator import generate_batch
from pipeline.main import main
from pipeline.models import CSV_COLUMNS
from pipeline.processing.transformations import process_transactions
from tests.factories import BNP_1, BNP_2, DEUTSCHE, make_transaction

pytestmark = pytest.mark.integration


def query(db, sql):
    return db.execute(sql).fetchall()


def count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def totals(db, sql):
    return dict(query(db, sql))


SENT_BY_IBAN = "SELECT iban, total FROM total_sent_by_iban"
RECEIVED_BY_IBAN = "SELECT iban, total FROM total_received_by_iban"
SENT_BY_BANK = "SELECT b.name, t.total FROM total_sent_by_bank t JOIN banks b ON b.id = t.bank_id"


def write_csv(path, transactions):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(tx.to_row() for tx in transactions)


def test_valeurs_attendues_calculees_a_la_main(pipeline_env, tmp_path):
    """Fichier écrit à la main : tous les résultats attendus sont connus d'avance."""
    db = pipeline_env
    write_csv(
        tmp_path / "manuel.csv",
        [
            make_transaction(BNP_1, DEUTSCHE, "1000.00"),
            make_transaction(BNP_2, DEUTSCHE, "6000.00"),
            make_transaction(DEUTSCHE, BNP_1, "5000.00"),
            make_transaction(BNP_1, BNP_2, "250.50"),
        ],
    )

    assert main(["--input", str(tmp_path), "--save"]) == 0

    assert totals(db, SENT_BY_IBAN) == {
        BNP_1.iban: Decimal("1250.50"),
        BNP_2.iban: Decimal("6000.00"),
        DEUTSCHE.iban: Decimal("5000.00"),
    }
    assert totals(db, SENT_BY_BANK) == {
        "BNP Paribas": Decimal("7250.50"),
        "Deutsche Bank": Decimal("5000.00"),
    }
    assert totals(db, RECEIVED_BY_IBAN) == {
        DEUTSCHE.iban: Decimal("7000.00"),
        BNP_1.iban: Decimal("5000.00"),
        BNP_2.iban: Decimal("250.50"),
    }
    assert query(db, "SELECT montant FROM transactions WHERE flag_montant_eleve") == [
        (Decimal("6000.00"),)
    ]
    assert query(db, "SELECT file_name, nb_transactions FROM processed_files") == [
        ("manuel.csv", 4)
    ]


def test_pipeline_complete_avec_generation(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    assert main(["--input", str(tmp_path), "--generate", "5", "--seed", "42", "--save"]) == 0

    lots = [generate_batch(42, i) for i in range(5)]
    attendu = process_transactions([tx for lot in lots for tx in lot])

    assert count(db, "processed_files") == 5
    assert count(db, "transactions") == sum(len(lot) for lot in lots)
    assert totals(db, SENT_BY_IBAN) == attendu.sent_by_iban
    assert totals(db, SENT_BY_BANK) == attendu.sent_by_bank
    assert totals(db, RECEIVED_BY_IBAN) == attendu.received_by_iban
    assert count(db, "transactions WHERE flag_montant_eleve") == sum(
        ft.is_flagged for ft in attendu.transactions
    )

    sortie = capsys.readouterr().out
    for etape in ["[1/4]", "[2/4]", "[3/4]", "[4/4]", "terminée avec succès"]:
        assert etape in sortie


def test_relance_sans_doublon(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "3", "--save"]
    assert main(args) == 0
    avant = (count(db, "transactions"), totals(db, SENT_BY_IBAN))
    capsys.readouterr()

    assert main(args) == 0  # mêmes fichiers régénérés : rien ne doit changer

    assert (count(db, "transactions"), totals(db, SENT_BY_IBAN)) == avant
    assert count(db, "processed_files") == 3
    sortie = capsys.readouterr().out
    assert sortie.count("déjà traité, ignoré") == 3
    assert "0 nouveau(x) fichier(s) à traiter sur 3" in sortie


def test_seul_un_nouveau_fichier_est_traite(pipeline_env, tmp_path):
    db = pipeline_env
    assert main(["--input", str(tmp_path), "--generate", "2", "--save"]) == 0
    write_csv(tmp_path / "nouveau.csv", [make_transaction(BNP_1, DEUTSCHE, "10.00")])

    assert main(["--input", str(tmp_path), "--save"]) == 0

    assert count(db, "processed_files") == 3
    assert query(db, "SELECT file_name FROM processed_files ORDER BY id DESC LIMIT 1") == [
        ("nouveau.csv",)
    ]


def test_fichier_invalide_rien_n_est_insere(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    write_csv(tmp_path / "a_valide.csv", [make_transaction()])
    (tmp_path / "b_invalide.csv").write_text(
        ",".join(CSV_COLUMNS) + "\n" + "x,y,z,w,v,u,t\n", encoding="utf-8"
    )

    assert main(["--input", str(tmp_path), "--save"]) == 1

    assert count(db, "processed_files") == 0
    erreur = capsys.readouterr().err
    assert "b_invalide.csv" in erreur
    assert "ligne 2" in erreur
    assert "Aucune donnée n'a été insérée" in erreur
