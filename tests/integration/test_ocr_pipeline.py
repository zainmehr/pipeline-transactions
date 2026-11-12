"""Niveau 3, option 2 : relevés au format image, lus par OCR, de bout en bout.

Les images rejoignent le même flux que les CSV (récupération locale ou S3, validation,
traitement, insertion) : les résultats en base doivent être exactement ceux des
transactions dessinées sur les images, sans double traitement.
"""

import shutil
from decimal import Decimal

import pytest

from pipeline.generation.generator import generate_batch
from pipeline.generation.images import image_transactions
from pipeline.main import main
from pipeline.processing.transformations import process_transactions

pytestmark = [pytest.mark.integration, pytest.mark.ocr, pytest.mark.usefixtures("tesseract")]

NO_WAIT = ["--retry-delay", "0"]
SENT_BY_IBAN = "SELECT iban, total FROM total_sent_by_iban"
RECEIVED_BY_IBAN = "SELECT iban, total FROM total_received_by_iban"
SENT_BY_BANK = "SELECT b.name, t.total FROM total_sent_by_bank t JOIN banks b ON b.id = t.bank_id"


def count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def totals(db, sql):
    return dict(db.execute(sql).fetchall())


def assert_results_match(db, transactions):
    attendu = process_transactions(transactions)
    assert totals(db, SENT_BY_IBAN) == attendu.sent_by_iban
    assert totals(db, RECEIVED_BY_IBAN) == attendu.received_by_iban
    assert totals(db, SENT_BY_BANK) == attendu.sent_by_bank
    for table in ("total_sent_by_iban", "total_received_by_iban"):
        ecarts = db.execute(
            f"SELECT COUNT(*) FROM {table} t FULL JOIN v_{table} v USING (iban) "
            "WHERE t.total IS DISTINCT FROM v.total"
        ).fetchone()[0]
        assert ecarts == 0


def test_images_traitees_depuis_un_dossier_local(pipeline_env, tmp_path):
    db = pipeline_env

    assert main(["--input", str(tmp_path), "--images", "2", "--seed", "42", "--save"]) == 0

    attendues = image_transactions(42, 0) + image_transactions(42, 1)
    assert db.execute(
        "SELECT file_name, source_type, nb_transactions FROM processed_files ORDER BY id"
    ).fetchall() == [("releve_001.png", "image", 3), ("releve_002.png", "image", 3)]
    assert count(db, "transactions") == 6
    assert_results_match(db, attendues)


def test_flags_corrects_apres_ocr(pipeline_env, tmp_path):
    """Cas limites de la première image : 4999,99 et 5000,00 non flaggés, 5000,01 flaggé."""
    db = pipeline_env
    assert main(["--input", str(tmp_path), "--images", "1", "--save"]) == 0

    flags = db.execute(
        "SELECT montant, flag_montant_eleve FROM transactions ORDER BY montant"
    ).fetchall()
    assert flags == [
        (Decimal("4999.99"), False),
        (Decimal("5000.00"), False),
        (Decimal("5000.01"), True),
    ]


def test_csv_et_images_dans_le_meme_flux(pipeline_env, tmp_path):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--generate", "2", "--images", "2", "--seed", "7"]

    assert main([*args, "--save"]) == 0

    attendues = [tx for i in range(2) for tx in generate_batch(7, i)]
    attendues += image_transactions(7, 0) + image_transactions(7, 1)
    types = dict(
        db.execute("SELECT source_type, COUNT(*) FROM processed_files GROUP BY 1").fetchall()
    )
    assert types == {"csv": 2, "image": 2}
    assert count(db, "transactions") == len(attendues)
    assert_results_match(db, attendues)

    # Relance : aucune image ni aucun CSV n'est traité une seconde fois.
    assert main([*args, "--save"]) == 0
    assert count(db, "processed_files") == 4
    assert count(db, "transactions") == len(attendues)
    assert_results_match(db, attendues)


def test_image_renommee_non_retraitee(pipeline_env, tmp_path, capsys):
    db = pipeline_env
    assert main(["--input", str(tmp_path), "--images", "1", "--save"]) == 0
    shutil.copy(tmp_path / "releve_001.png", tmp_path / "copie_du_releve.jpg")

    assert main(["--input", str(tmp_path), "--save"]) == 0

    assert "copie_du_releve.jpg : déjà traité, ignoré" in capsys.readouterr().out
    assert count(db, "processed_files") == 1


def test_image_illisible_rien_n_est_insere(pipeline_env, tmp_path, capsys):
    """Tout ou rien : une image illisible bloque l'insertion de tous les fichiers du lot."""
    db = pipeline_env
    (tmp_path / "zz_photo.png").write_bytes(b"ce n'est pas une image")

    assert main(["--input", str(tmp_path), "--generate", "1", "--images", "1", "--save"]) == 1

    erreur = capsys.readouterr().err
    assert "Image invalide : zz_photo.png" in erreur
    assert "Aucune donnée n'a été insérée" in erreur
    assert count(db, "processed_files") == 0


def test_images_envoyees_et_lues_depuis_s3(pipeline_env, s3, tmp_path):
    db = pipeline_env
    config, client = s3
    args = ["--input", str(tmp_path), "--images", "2", "--seed", "42", "--upload", "--source"]

    assert main([*args, "s3", "--save", *NO_WAIT]) == 0

    objets = client.list_objects_v2(Bucket=config.bucket)["Contents"]
    assert sorted(o["Key"].rsplit("/", 1)[-1] for o in objets) == [
        "releve_001.png",
        "releve_002.png",
    ]
    head = client.head_object(Bucket=config.bucket, Key=objets[0]["Key"])
    assert head["ContentType"] == "image/png"
    origines = [row[0] for row in db.execute("SELECT file_name FROM processed_files")]
    assert all(o.startswith(f"s3://{config.bucket}/transactions/") for o in origines)
    assert_results_match(db, image_transactions(42, 0) + image_transactions(42, 1))

    # Les images sont lues depuis le bucket : le dossier local n'est plus nécessaire.
    shutil.rmtree(tmp_path)
    assert main(["--source", "s3", "--save", *NO_WAIT]) == 0
    assert count(db, "processed_files") == 2


def test_retry_sur_la_recuperation_d_une_image(pipeline_env, s3, tmp_path, capsys):
    db = pipeline_env
    args = ["--input", str(tmp_path), "--images", "1", "--upload", "--source", "s3", "--save"]

    assert main([*args, "--simulate-download-failures", "2", *NO_WAIT]) == 0

    sortie = capsys.readouterr().out
    assert "tentative 1/3 de « récupération de releve_001.png » échouée" in sortie
    assert count(db, "processed_files") == 1


def test_upload_only_puis_traitement_separe(pipeline_env, s3, tmp_path, capsys):
    """Producteur (--upload-only) puis traitement planifié (--source s3 --save), comme
    le CronJob Kubernetes de l'environnement de production."""
    db = pipeline_env
    producteur = ["--input", str(tmp_path), "--generate", "1", "--images", "1", "--upload"]

    assert main([*producteur, "--upload-only", "--save"]) == 0
    assert "Traitement non demandé (--upload-only)" in capsys.readouterr().out
    assert count(db, "processed_files") == 0

    assert main(["--source", "s3", "--save", *NO_WAIT]) == 0
    assert count(db, "processed_files") == 2
