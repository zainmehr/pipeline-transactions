from pipeline.generation.generator import generate_files
from pipeline.main import main


def test_pipeline_sur_fichiers_valides(tmp_path, capsys):
    generate_files(tmp_path, nb_files=2, seed=42)
    assert main(["--input", str(tmp_path)]) == 0
    sortie = capsys.readouterr().out
    assert "transactions_001.csv" in sortie
    assert "Transactions flaggées" in sortie


def test_pipeline_echoue_sur_fichier_invalide(tmp_path, capsys):
    generate_files(tmp_path, nb_files=1, seed=42)
    (tmp_path / "zz_invalide.csv").write_text("colonne\nvaleur\n", encoding="utf-8")
    assert main(["--input", str(tmp_path)]) == 1
    assert "zz_invalide.csv" in capsys.readouterr().err


def test_pipeline_echoue_sans_fichier(tmp_path, capsys):
    assert main(["--input", str(tmp_path)]) == 1
    assert "aucun fichier CSV" in capsys.readouterr().err


def test_pipeline_echoue_si_dossier_absent(tmp_path, capsys):
    assert main(["--input", str(tmp_path / "absent")]) == 1
    assert "Dossier introuvable" in capsys.readouterr().err


def test_pipeline_avec_generation_sans_base(tmp_path, capsys):
    assert main(["--input", str(tmp_path / "data"), "--generate", "3", "--seed", "7"]) == 0
    assert len(list((tmp_path / "data").glob("*.csv"))) == 3
    sortie = capsys.readouterr().out
    assert "transactions_003.csv généré" in sortie
    assert "Pipeline terminée avec succès" in sortie


def test_source_inconnue_refusee(tmp_path, capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--input", str(tmp_path), "--source", "ftp"])
    assert "invalid choice" in capsys.readouterr().err


def test_images_sans_tesseract(tmp_path, capsys, monkeypatch):
    """Sans Tesseract, la pipeline s'arrête avec un message clair, avant tout traitement."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert main(["--input", str(tmp_path), "--images", "1"]) == 1
    erreur = capsys.readouterr().err
    assert "Tesseract est introuvable" in erreur
    assert "Aucune donnée n'a été insérée" in erreur


def test_run_ocr_sans_tesseract(monkeypatch):
    import pytest

    from pipeline.loading.ocr import OCRUnavailableError, run_ocr

    monkeypatch.setenv("PATH", "")
    with pytest.raises(OCRUnavailableError):
        run_ocr(b"image")


def test_upload_only_necessite_upload(tmp_path, capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--input", str(tmp_path), "--upload-only"])
    assert "--upload-only nécessite --upload" in capsys.readouterr().err
