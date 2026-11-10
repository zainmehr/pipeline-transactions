import pytest

from pipeline.generation.generator import generate_batch, generate_files
from pipeline.loading.csv_loader import InvalidCSVError, list_csv_files, load_csv, parse_csv
from pipeline.models import CSV_COLUMNS
from tests.factories import make_transaction

HEADER = ",".join(CSV_COLUMNS)


def write(path, *lines, encoding="utf-8"):
    path.write_text("\n".join(lines) + "\n", encoding=encoding)
    return path


def csv_line(transaction):
    return ",".join(transaction.to_row()[col] for col in CSV_COLUMNS)


def test_relit_exactement_les_fichiers_generes(tmp_path):
    paths = generate_files(tmp_path, nb_files=3, seed=42)
    for index, path in enumerate(paths):
        assert load_csv(path) == generate_batch(seed=42, batch_index=index)


def test_fichier_avec_bom_accepte(tmp_path):
    path = write(tmp_path / "bom.csv", HEADER, csv_line(make_transaction()), encoding="utf-8-sig")
    assert load_csv(path) == [make_transaction()]


def test_fichier_avec_entete_seule_retourne_liste_vide(tmp_path):
    assert load_csv(write(tmp_path / "vide.csv", HEADER)) == []


def test_fichier_completement_vide_rejete(tmp_path):
    path = tmp_path / "vide.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(InvalidCSVError, match="en-tête absent"):
        load_csv(path)


def test_colonne_manquante_rejetee(tmp_path):
    path = write(tmp_path / "f.csv", HEADER.replace(",montant", ""))
    with pytest.raises(InvalidCSVError, match="colonnes manquantes : montant"):
        load_csv(path)


def test_erreurs_rapportees_avec_numero_de_ligne(tmp_path):
    valide = csv_line(make_transaction())
    invalide = csv_line(make_transaction()).replace("100.00", "abc")
    path = write(tmp_path / "erreurs.csv", HEADER, valide, invalide, valide, invalide)

    with pytest.raises(InvalidCSVError) as excinfo:
        load_csv(path)

    error = excinfo.value
    assert error.path == path
    assert len(error.errors) == 2
    assert error.errors[0].startswith("ligne 3 :")
    assert error.errors[1].startswith("ligne 5 :")
    assert "erreurs.csv" in str(error)


def test_encodage_invalide_rejete(tmp_path):
    path = tmp_path / "latin1.csv"
    path.write_bytes((HEADER + "\n").encode() + "Crédit".encode("latin-1"))
    with pytest.raises(InvalidCSVError, match="encodage invalide"):
        load_csv(path)


def test_list_csv_files_trie_et_filtre(tmp_path):
    for name in ["b.csv", "a.csv", "notes.txt"]:
        (tmp_path / name).write_text(HEADER, encoding="utf-8")
    (tmp_path / "dossier.csv").mkdir()
    assert [p.name for p in list_csv_files(tmp_path)] == ["a.csv", "b.csv"]


def test_list_csv_files_dossier_inexistant(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_csv_files(tmp_path / "absent")


def test_parse_csv_depuis_un_contenu_en_memoire():
    contenu = (HEADER + "\n" + csv_line(make_transaction()) + "\n").encode("utf-8")
    assert parse_csv(contenu, "s3://bucket/transactions/f.csv") == [make_transaction()]


def test_parse_csv_erreur_nomme_la_source():
    with pytest.raises(InvalidCSVError, match="Fichier CSV invalide : f.csv"):
        parse_csv(b"colonne\nvaleur\n", "s3://bucket/transactions/f.csv")


def test_parse_csv_encodage_invalide():
    with pytest.raises(InvalidCSVError, match="encodage invalide"):
        parse_csv("Crédit".encode("latin-1"), "f.csv")
