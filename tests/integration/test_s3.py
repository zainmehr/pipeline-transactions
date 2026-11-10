"""Stockage S3 : création du bucket, envoi des fichiers et vérification, sur un vrai
service compatible S3 (RustFS en local et en CI)."""

import pytest

from pipeline.generation.generator import generate_files
from pipeline.loading.csv_loader import compute_file_hash
from pipeline.main import main
from pipeline.storage.s3 import (
    ensure_bucket,
    list_keys,
    object_hash,
    object_key,
    upload_file,
)

pytestmark = pytest.mark.integration


def test_creation_du_bucket_idempotente(s3):
    config, client = s3
    assert ensure_bucket(client, config.bucket) is True
    assert ensure_bucket(client, config.bucket) is False  # déjà existant : rien à faire


def test_envoi_et_lecture_des_metadonnees(s3, tmp_path):
    config, client = s3
    ensure_bucket(client, config.bucket)
    [path] = generate_files(tmp_path, nb_files=1)
    file_hash = compute_file_hash(path)

    key = upload_file(client, config.bucket, path.name, path.read_bytes(), file_hash)

    assert key == object_key("transactions_001.csv", file_hash)
    assert list_keys(client, config.bucket) == [key]
    assert object_hash(client, config.bucket, key) == file_hash
    contenu = client.get_object(Bucket=config.bucket, Key=key)["Body"].read()
    assert contenu == path.read_bytes()


def test_objet_absent(s3):
    config, client = s3
    ensure_bucket(client, config.bucket)
    assert object_hash(client, config.bucket, "transactions/absent.csv") is None
    assert list_keys(client, config.bucket) == []


def test_pipeline_avec_envoi_vers_s3(s3, tmp_path, capsys):
    config, client = s3

    assert main(["--input", str(tmp_path), "--generate", "3", "--upload"]) == 0

    sortie = capsys.readouterr().out
    assert f"bucket {config.describe()} créé" in sortie
    assert sortie.count("envoyé vers s3://") == 3
    assert "vérification : 3/3 fichier(s) présent(s) et intègre(s)" in sortie
    paths = sorted(tmp_path.glob("*.csv"))
    keys = [object_key(path.name, compute_file_hash(path)) for path in paths]
    assert sorted(list_keys(client, config.bucket)) == sorted(keys)
    for path, key in zip(paths, keys, strict=True):
        assert object_hash(client, config.bucket, key) == compute_file_hash(path)


def test_meme_nom_contenu_different_aucun_ecrasement(s3, tmp_path):
    """Un fichier régénéré avec une autre graine porte le même nom : les deux versions
    sont conservées, aucune ne peut en écraser une autre avant d'avoir été traitée."""
    config, client = s3
    args = ["--input", str(tmp_path), "--generate", "1", "--upload"]
    assert main([*args, "--seed", "1"]) == 0
    assert main([*args, "--seed", "2"]) == 0
    keys = list_keys(client, config.bucket)
    assert len(keys) == 2
    assert all(key.endswith("/transactions_001.csv") for key in keys)


def test_renvoi_du_meme_fichier_idempotent(s3, tmp_path):
    config, client = s3
    args = ["--input", str(tmp_path), "--generate", "2", "--upload"]
    assert main(args) == 0
    assert main(args) == 0
    assert len(list_keys(client, config.bucket)) == 2


def test_commande_list_s3(s3, tmp_path, capsys):
    from pipeline.storage.s3 import main as list_s3

    config, _ = s3
    assert main(["--input", str(tmp_path), "--generate", "2", "--upload"]) == 0
    capsys.readouterr()

    assert list_s3() == 0
    sortie = capsys.readouterr().out
    assert f"s3://{config.bucket}/transactions/" in sortie
    assert "/transactions_002.csv" in sortie
    assert "2 fichier(s)" in sortie
