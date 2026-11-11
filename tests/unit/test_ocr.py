"""Lecture réelle des images par Tesseract (OCR).

Ces tests sont ignorés en local si Tesseract n'est pas installé, mais échouent lorsque
REQUIRE_OCR=1 (cas de la CI), pour qu'un problème ne passe jamais inaperçu.
"""

import io

import pytest
from PIL import Image, ImageFilter

from pipeline.generation.images import image_transactions, render_image, statement_lines
from pipeline.loading.image_loader import InvalidImageError, parse_image
from pipeline.loading.ocr import OCRError, OCRUnavailableError, check_tesseract, run_ocr

pytestmark = [pytest.mark.ocr, pytest.mark.usefixtures("tesseract")]


def releve_png(seed, index):
    transactions = image_transactions(seed, index)
    return transactions, render_image(statement_lines(transactions, f"REL-{seed}-{index + 1:03d}"))


def test_texte_reconnu():
    _, image = releve_png(42, 0)
    texte = run_ocr(image)
    assert "Nombre de transactions : 3" in texte
    assert "Montant : 5 000,01 EUR" in texte


@pytest.mark.parametrize(("seed", "index"), [(42, 0), (42, 1), (7, 0), (2001, 2), (123, 4)])
def test_transactions_identiques_apres_ocr(seed, index):
    """L'OCR retrouve exactement les transactions dessinées sur l'image (accents compris)."""
    attendues, image = releve_png(seed, index)
    assert parse_image(image, "releve.png") == attendues


def test_image_scannee_de_qualite_moyenne():
    """JPEG compressé, légèrement flou et penché, comme un document scanné."""
    attendues, image = releve_png(42, 1)
    scan = (
        Image.open(io.BytesIO(image))
        .filter(ImageFilter.GaussianBlur(0.8))
        .rotate(0.6, fillcolor=255, expand=True)
        .convert("RGB")
    )
    buffer = io.BytesIO()
    scan.save(buffer, format="JPEG", quality=60)
    assert parse_image(buffer.getvalue(), "scan.jpg") == attendues


def test_image_sans_releve():
    buffer = io.BytesIO()
    Image.new("L", (400, 200), 255).save(buffer, format="PNG")
    with pytest.raises(InvalidImageError, match="aucune transaction reconnue"):
        parse_image(buffer.getvalue(), "blanc.png")


def test_fichier_qui_n_est_pas_une_image():
    with pytest.raises(OCRError):
        run_ocr(b"ceci n'est pas une image")
    with pytest.raises(InvalidImageError, match="Image invalide : faux.png"):
        parse_image(b"ceci n'est pas une image", "faux.png")


def test_langue_absente(monkeypatch):
    monkeypatch.setenv("OCR_LANG", "klingon")
    with pytest.raises(OCRUnavailableError, match="klingon"):
        check_tesseract()
