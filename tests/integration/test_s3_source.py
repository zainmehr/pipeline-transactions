"""Lecture des fichiers depuis le stockage S3 (2.3) et retry sur leur récupération (2.4)."""

import shutil
from decimal import Decimal

import pytest

from pipeline.generation.generator import generate_batch, generate_files
from pipeline.loading.csv_loader import compute_hash
from pipeline.loading.sources import fetch_s3_files
from pipeline.main import main
from pipeline.processing.transformations import process_transactions
from pipeline.retry import RetryError, RetryPolicy
from pipeline.storage.s3 import ensure_bucket, object_key, upload_file

pytestmark = pytest.mark.integration

FAST = RetryPolicy(max_attempts=3, initial_delay=0)
NO_WAIT = ["--retry-delay", "0"]


def count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def put(client, bucket, name, content, declared_hash="auto"):
    """Dépose un objet ; declared_hash=None simule un fichier déposé par un autre outil."""
    metadata = {}
    if declared_hash == "auto":
        metadata["sha256"] = compute_hash(content)
    elif declared_hash is not None:
        metadata["sha256"] = declared_hash
    key = object_key(name, compute_hash(content))
    client.put_object(Bucket=bucket, Key=key, Body=content, Metadata=metadata)


@pytest.fixture
def bucket_with_files(s3, tmp_path):
    """Bucket contenant 3 fichiers générés (graine 42), envoyés comme le fait la pipeline."""
    config, client = s3
    ensure_bucket(client, config.bucket)
    for path in generate_files(tmp_path / "gen", nb_files=3, seed=42):
        content = path.read_bytes()
        upload_file(client, config.bucket, path.name, content, compute_hash(content))
    return config, client


# --- Récupération (fetch_s3_files) ------------------------------------------------


def test_recuperation_des_nouveaux_fichiers(bucket_with_files):
    config, client = bucket_with_files
    files, total = fetch_s3_files(client, config.bucket, set(), FAST, log=lambda _: None)

    assert total == 3
    assert [f.name for f in files] == [f"transactions_00{i}.csv" for i in (1, 2, 3)]
    key = object_key("transactions_001.csv", files[0].file_hash)
    assert files[0].origin == f"s3://{config.bucket}/{key}"
    assert files[0].file_hash == compute_hash(files[0].content)


def test_fichier_deja_traite_ignore_sans_telechargement(bucket_with_files):
    config, client = bucket_with_files
    first, _ = fetch_s3_files(client, config.bucket, set(), FAST, log=lambda _: None)
    downloads = []

    files, total = fetch_s3_files(
        client,
        config.bucket,
        {first[0].file_hash},
        FAST,
        inject_failure=downloads.append,
        log=lambda _: None,
    )

    assert total == 3
    assert [f.name for f in files] == ["transactions_002.csv", "transactions_003.csv"]
    assert downloads == ["transactions_002.csv", "transactions_003.csv"]


def test_fichier_sans_metadonnee_telecharge_et_identifie(s3):
    config, client = s3
    ensure_bucket(client, config.bucket)
    put(client, config.bucket, "externe.csv", b"contenu", declared_hash=None)

    [file], _ = fetch_s3_files(client, config.bucket, set(), FAST, log=lambda _: None)

    assert file.file_hash == compute_hash(b"contenu")


def test_contenu_altere_reessaye_puis_arret(s3):
    config, client = s3
    ensure_bucket(client, config.bucket)
    put(client, config.bucket, "f.csv", b"contenu", declared_hash="0" * 64)
    logs = []

    with pytest.raises(RetryError, match="contenu reçu altéré"):
        fetch_s3_files(client, config.bucket, set(), FAST, log=logs.append)
    assert sum("tentative" in line for line in logs) == 3


def test_seuls_les_csv_sont_recuperes(s3):
    config, client = s3
    ensure_bucket(client, config.bucket)
    put(client, config.bucket, "a.csv", b"a")
    client.put_object(Bucket=config.bucket, Key="transactions/notes.txt", Body=b"x")

    files, total = fetch_s3_files(client, config.bucket, set(), FAST, log=lambda _: None)
    assert [f.name for f in files] == ["a.csv"]
    assert total == 1


def test_pannes_temporaires_puis_succes(bucket_with_files):
    config, client = bucket_with_files
    remaining = [2]
    logs = []

    def panne(name):
        if remaining[0]:
            remaining[0] -= 1
            from pipeline.storage.s3 import TransientS3Error

            raise TransientS3Error(f"indisponible pendant {name}")

    files, _ = fetch_s3_files(client, config.bucket, set(), FAST, panne, log=logs.append)

    assert len(files) == 3
    assert "tentative 1/3 de « récupération de transactions_001.csv »" in logs[0]
    assert "tentative 2/3" in logs[1]


# --- Pipeline complète depuis S3 -------------------------------------------------


def test_pipeline_lit_depuis_s3_sans_dependre_du_disque_local(pipeline_env, s3, tmp_path):
    db = pipeline_env
    config, _ = s3
    local = tmp_path / "data"
    assert main(["--input", str(local), "--generate", "3", "--upload", *NO_WAIT]) == 0
    shutil.rmtree(local)  # plus aucun fichier local : tout doit venir du bucket

    assert main(["--input", str(local), "--source", "s3", "--save", *NO_WAIT]) == 0

    attendu = process_transactions([tx for i in range(3) for tx in generate_batch(42, i)])
    assert count(db, "processed_files") == 3
    totaux = dict(db.execute("SELECT iban, total FROM total_sent_by_iban").fetchall())
    assert totaux == attendu.sent_by_iban
    origines = [row[0] for row in db.execute("SELECT file_name FROM processed_files ORDER BY id")]
    assert all(origine.startswith(f"s3://{config.bucket}/transactions/") for origine in origines)
    assert [o.rsplit("/", 1)[-1] for o in origines] == [
        f"transactions_00{i}.csv" for i in (1, 2, 3)
    ]


def test_pipeline_complete_puis_relance_sans_doublon(pipeline_env, s3, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "3", "--upload", "--source", "s3", "--save"]
    assert main([*args, *NO_WAIT]) == 0
    capsys.readouterr()

    assert main([*args, *NO_WAIT]) == 0

    assert count(db, "processed_files") == 3
    sortie = capsys.readouterr().out
    assert sortie.count("déjà traité, ignoré") == 3
    assert "0 nouveau(x) fichier(s) à traiter sur 3" in sortie
    for etape in ["[4/6] Récupération des fichiers", "source : http"]:
        assert etape in sortie


def test_pannes_de_recuperation_simulees_puis_succes(pipeline_env, s3, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "2", "--upload", "--source", "s3", "--save"]

    assert main([*args, *NO_WAIT, "--simulate-download-failures", "2"]) == 0

    sortie = capsys.readouterr().out
    assert "[simulation] stockage S3 indisponible pendant la récupération" in sortie
    assert "tentative 2/3 de « récupération de transactions_001.csv »" in sortie
    assert count(db, "processed_files") == 2


def test_fichier_inaccessible_arret_propre_puis_reprise(pipeline_env, s3, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "2", "--upload", "--source", "s3", "--save"]

    assert main([*args, *NO_WAIT, "--simulate-download-failures", "10"]) == 1

    sortie = capsys.readouterr()
    assert "ÉCHEC : tentative 3/3 de « récupération de transactions_001.csv »" in sortie.out
    assert "stockage S3 inaccessible" in sortie.err
    assert "aucune donnée n'a été insérée" in sortie.err
    assert count(db, "processed_files") == 0

    assert main([*args, *NO_WAIT]) == 0  # le stockage est revenu : reprise complète
    assert count(db, "processed_files") == 2


def test_bucket_vide(pipeline_env, s3, tmp_path, capsys):
    config, client = s3
    ensure_bucket(client, config.bucket)
    assert main(["--input", str(tmp_path), "--source", "s3", "--save", *NO_WAIT]) == 1
    assert "aucun fichier CSV ou image dans" in capsys.readouterr().err


def test_fichier_invalide_dans_le_bucket(pipeline_env, s3, tmp_path, capsys):
    db = pipeline_env
    config, client = s3
    ensure_bucket(client, config.bucket)
    put(client, config.bucket, "casse.csv", b"colonne\nvaleur\n")

    assert main(["--input", str(tmp_path), "--source", "s3", "--save", *NO_WAIT]) == 1

    assert "Fichier CSV invalide : casse.csv" in capsys.readouterr().err
    assert count(db, "processed_files") == 0


def test_montants_lus_depuis_s3_exacts(pipeline_env, s3, tmp_path):
    """Les montants traversent l'envoi et la récupération sans aucune altération."""
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "1", "--upload", "--source", "s3", "--save"]
    assert main([*args, *NO_WAIT]) == 0
    flags = dict(db.execute("SELECT montant, flag_montant_eleve FROM transactions").fetchall())
    assert flags[Decimal("5000.00")] is False
    assert flags[Decimal("5000.01")] is True
