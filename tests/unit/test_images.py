"""Génération des relevés au format image et choix du chargement selon le type de fichier."""

from datetime import datetime
from decimal import Decimal

import pytest

from pipeline.generation.generator import EDGE_CASE_AMOUNTS
from pipeline.generation.images import (
    TRANSACTIONS_PER_IMAGE,
    format_amount,
    format_iban,
    generate_image_files,
    image_transactions,
    render_image,
    statement_lines,
)
from pipeline.loading.csv_loader import InvalidCSVError
from pipeline.loading.parsers import (
    content_type,
    is_supported,
    list_input_files,
    parse_file,
    source_type,
)
from tests.factories import make_transaction

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    ("montant", "attendu"),
    [
        ("0.50", "0,50 EUR"),
        ("999.00", "999,00 EUR"),
        ("5000.01", "5 000,01 EUR"),
        ("1234567.89", "1 234 567,89 EUR"),
    ],
)
def test_format_des_montants(montant, attendu):
    assert format_amount(Decimal(montant)) == attendu


def test_format_des_iban():
    assert format_iban("FR7630004000031234567890143") == "FR76 3000 4000 0312 3456 7890 143"


def test_lignes_du_releve():
    lignes = statement_lines([make_transaction(montant="5000.01")], "REL-1")
    assert lignes[:3] == ["RELEVÉ D'OPÉRATIONS", "Référence : REL-1", "Nombre de transactions : 1"]
    assert "Transaction 1" in lignes
    assert "Date : 01/01/2026 12:00:00" in lignes
    assert "Montant : 5 000,01 EUR" in lignes


def test_transactions_des_images_deterministes():
    assert image_transactions(42, 1) == image_transactions(42, 1)
    assert image_transactions(42, 1) != image_transactions(43, 1)
    assert len(image_transactions(42, 1)) == TRANSACTIONS_PER_IMAGE


def test_premiere_image_contient_les_cas_limites_du_flag():
    montants = tuple(tx.montant for tx in image_transactions(42, 0))
    assert montants == EDGE_CASE_AMOUNTS


def test_dates_des_images_dans_la_meme_periode_que_les_csv():
    for tx in image_transactions(42, 0) + image_transactions(42, 4):
        assert datetime(2026, 1, 1) <= tx.datetime_transaction < datetime(2026, 2, 1)


def test_rendu_png_deterministe():
    lignes = statement_lines(image_transactions(42, 0), "REL-42-001")
    image = render_image(lignes)
    assert image.startswith(PNG_SIGNATURE)
    assert render_image(lignes) == image


def test_generation_des_fichiers_images(tmp_path):
    chemins = generate_image_files(tmp_path, nb_images=2, seed=42)
    assert [p.name for p in chemins] == ["releve_001.png", "releve_002.png"]
    assert chemins[0].read_bytes().startswith(PNG_SIGNATURE)
    # Même graine => mêmes fichiers (donc même empreinte : pas de double traitement).
    assert generate_image_files(tmp_path / "bis", 2, 42)[0].read_bytes() == chemins[0].read_bytes()


def test_generation_refuse_zero_image(tmp_path):
    with pytest.raises(ValueError):
        generate_image_files(tmp_path, nb_images=0)


# --- Type de fichier -----------------------------------------------------------


@pytest.mark.parametrize(
    ("nom", "type_attendu", "mime"),
    [
        ("a.csv", "csv", "text/csv"),
        ("releve.png", "image", "image/png"),
        ("SCAN.JPG", "image", "image/jpeg"),
        ("scan.jpeg", "image", "image/jpeg"),
        ("scan.tiff", "image", "image/tiff"),
    ],
)
def test_type_des_fichiers(nom, type_attendu, mime):
    assert is_supported(nom)
    assert source_type(nom) == type_attendu
    assert content_type(nom) == mime


def test_fichier_non_pris_en_charge():
    assert not is_supported("notes.txt")
    with pytest.raises(ValueError):
        source_type("notes.txt")


def test_liste_des_fichiers_d_entree(tmp_path):
    for nom in ("b.csv", "a.png", "c.jpg", "notes.txt"):
        (tmp_path / nom).write_bytes(b"x")
    (tmp_path / "dossier.csv").mkdir()
    assert [p.name for p in list_input_files(tmp_path)] == ["a.png", "b.csv", "c.jpg"]


def test_liste_dossier_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_input_files(tmp_path / "absent")


def test_parse_file_choisit_le_chargement():
    attendues = image_transactions(42, 1)
    texte = "\n".join(statement_lines(attendues, "REL"))
    assert parse_file(b"image", "releve.png", ocr=lambda _: texte) == attendues
    with pytest.raises(InvalidCSVError, match="Fichier CSV invalide"):
        parse_file(b"colonne\nvaleur\n", "donnees.csv", ocr=lambda _: pytest.fail("pas d'OCR"))
