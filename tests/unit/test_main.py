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
