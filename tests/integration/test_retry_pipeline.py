"""Retry lors de l'insertion en base : pannes simulées, reprise et absence de doublons."""

import psycopg
import pytest

from pipeline.generation.generator import generate_batch
from pipeline.main import main
from pipeline.processing.transformations import process_transactions
from pipeline.storage.database import insert_processed_file

pytestmark = pytest.mark.integration

FAST = ["--retry-delay", "0"]


def count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def totals(db, sql):
    return dict(db.execute(sql).fetchall())


def test_panne_pendant_l_insertion_annule_tout(db):
    """Une panne juste avant la validation ne laisse aucune donnée partielle en base."""
    result = process_transactions(generate_batch(42, 0))

    def panne():
        raise psycopg.OperationalError("connexion perdue")

    with pytest.raises(psycopg.OperationalError):
        insert_processed_file(db, "f1.csv", "a" * 64, result, before_commit=panne)

    for table in ["processed_files", "transactions", "accounts", "total_sent_by_iban"]:
        assert count(db, table) == 0

    # La nouvelle tentative insère le fichier une seule fois.
    insert_processed_file(db, "f1.csv", "a" * 64, result)
    assert count(db, "processed_files") == 1
    assert count(db, "transactions") == len(result.transactions)


def test_pannes_temporaires_puis_succes(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "3", "--save", *FAST]

    assert main([*args, "--simulate-insert-failures", "2"]) == 0

    sortie = capsys.readouterr().out
    assert "tentative 1/3" in sortie
    assert "tentative 2/3" in sortie
    assert "[simulation] connexion perdue pendant l'insertion de transactions_001.csv" in sortie
    assert sortie.count("inséré (id=") == 3

    # Aucune donnée en double malgré les tentatives : sommes = sommes recalculées.
    lots = [generate_batch(42, i) for i in range(3)]
    attendu = process_transactions([tx for lot in lots for tx in lot])
    assert count(db, "processed_files") == 3
    assert count(db, "transactions") == sum(len(lot) for lot in lots)
    assert totals(db, "SELECT iban, total FROM total_sent_by_iban") == attendu.sent_by_iban


def test_arret_apres_le_maximum_puis_reprise(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "3", "--save", *FAST]

    # 3 échecs consécutifs dès le 1er fichier : la pipeline s'arrête sans rien insérer.
    assert main([*args, "--simulate-insert-failures", "3", "--max-attempts", "3"]) == 1

    sortie = capsys.readouterr()
    assert "ÉCHEC : tentative 3/3" in sortie.out
    assert "après 3 tentative(s)" in sortie.err
    assert "reprendra le traitement" in sortie.err
    assert count(db, "processed_files") == 0

    # Relance sans panne : tous les fichiers sont insérés, une seule fois.
    assert main(args) == 0
    assert count(db, "processed_files") == 3


def test_reprise_apres_arret_en_cours_de_route(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "3", "--save", *FAST]
    # Le 1er fichier est déjà en base avant la panne.
    assert main(["--input", str(tmp_path), "--generate", "1", "--save", *FAST]) == 0
    capsys.readouterr()

    assert main([*args, "--simulate-insert-failures", "5", "--max-attempts", "2"]) == 1
    assert count(db, "processed_files") == 1  # rien de plus, rien en double

    assert main(args) == 0
    sortie = capsys.readouterr().out
    assert "transactions_001.csv : déjà traité, ignoré" in sortie
    assert count(db, "processed_files") == 3
