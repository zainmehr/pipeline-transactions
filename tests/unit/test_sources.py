import pytest

from pipeline.loading.csv_loader import compute_hash
from pipeline.loading.sources import SourceFile, exclude_processed, read_local_files
from pipeline.main import SimulatedFailures


def source(name, content=b"contenu"):
    return SourceFile(name, name, compute_hash(content), content)


def test_lecture_d_un_dossier_local(tmp_path):
    (tmp_path / "b.csv").write_bytes(b"deux")
    (tmp_path / "a.csv").write_bytes(b"un")
    (tmp_path / "notes.txt").write_bytes(b"ignore")

    files = read_local_files(tmp_path)

    assert [f.name for f in files] == ["a.csv", "b.csv"]
    assert files[0].content == b"un"
    assert files[0].file_hash == compute_hash(b"un")


def test_dossier_local_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_local_files(tmp_path / "absent")


def test_exclusion_des_fichiers_deja_traites():
    deja = source("a.csv", b"a")
    nouveau = source("b.csv", b"b")
    logs = []

    assert exclude_processed([deja, nouveau], {deja.file_hash}, log=logs.append) == [nouveau]
    assert "a.csv : déjà traité, ignoré" in logs[0]
    assert "1 nouveau(x) fichier(s) à traiter sur 2" in logs[-1]


def test_doublon_de_contenu_dans_le_meme_lot():
    original, copie = source("a.csv", b"meme"), source("copie.csv", b"meme")
    assert exclude_processed([original, copie], set(), log=lambda _: None) == [original]


def test_echecs_simules_puis_succes():
    failures = SimulatedFailures(2, lambda label: RuntimeError(f"panne {label}"))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="panne f.csv"):
            failures.trigger("f.csv")
    failures.trigger("f.csv")  # plus aucun échec à provoquer
    assert failures.hook("f.csv") is None


def test_echec_simule_differe():
    failures = SimulatedFailures(1, lambda label: RuntimeError(label))
    hook = failures.hook("f.csv")
    assert failures.remaining == 1  # rien ne se passe avant l'appel
    with pytest.raises(RuntimeError):
        hook()
    assert failures.remaining == 0
