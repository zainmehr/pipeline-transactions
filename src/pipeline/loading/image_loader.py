"""Chargement des relevés au format image.

    image ──OCR──> texte ──extraction──> lignes au format CSV ──validation──> transactions

Après l'OCR, chaque transaction extraite passe par exactement la même validation qu'une
ligne de fichier CSV (`parse_transaction`) : les images rejoignent ensuite le même flux de
traitement et d'insertion que les CSV.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pipeline.extraction.statement import StatementError, extract_rows
from pipeline.loading.csv_loader import InvalidCSVError
from pipeline.loading.ocr import OCRError, run_ocr
from pipeline.models import Transaction
from pipeline.validation import InvalidTransactionError, parse_transaction

IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


class InvalidImageError(InvalidCSVError):
    """Les transactions d'une image ne peuvent pas être extraites ou sont invalides."""

    title = "Image invalide"


def is_image(name: str) -> bool:
    return name.lower().endswith(IMAGE_EXTENSIONS)


def transactions_from_text(text: str, source: Path | str) -> list[Transaction]:
    """Extrait et valide les transactions d'un texte OCR (fonction pure)."""
    try:
        rows = extract_rows(text)
    except StatementError as error:
        raise InvalidImageError(source, error.errors) from error

    transactions: list[Transaction] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=1):
        try:
            transactions.append(parse_transaction(row))
        except InvalidTransactionError as error:
            errors.append(f"transaction {number} : {error}")
    if errors:
        raise InvalidImageError(source, errors)
    return transactions


def parse_image(
    content: bytes, source: Path | str, ocr: Callable[[bytes], str] = run_ocr
) -> list[Transaction]:
    """Lit une image par OCR, puis extrait et valide ses transactions.

    `ocr` est injectable pour tester l'extraction sans Tesseract.
    """
    try:
        text = ocr(content)
    except OCRError as error:
        raise InvalidImageError(source, [str(error)]) from error
    return transactions_from_text(text, source)
