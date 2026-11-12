"""Fixtures communes à tous les tests."""

import os

import pytest

from pipeline.loading.ocr import OCRUnavailableError, check_tesseract


@pytest.fixture(scope="session")
def tesseract():
    """Version de Tesseract. Les tests OCR sont ignorés en local si Tesseract n'est pas
    installé, mais échouent lorsque REQUIRE_OCR=1 (cas de la CI)."""
    try:
        return check_tesseract()
    except OCRUnavailableError as error:
        if os.environ.get("REQUIRE_OCR") == "1":
            pytest.fail(str(error))
        pytest.skip(f"{error} — installer tesseract-ocr et tesseract-ocr-fra")
