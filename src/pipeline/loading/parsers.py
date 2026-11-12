"""Choix du chargement selon le type de fichier : CSV ou image (OCR).

Les deux types de fichiers produisent la même chose, une liste de transactions validées :
la suite de la pipeline (traitement, insertion) ne fait aucune différence entre eux.

    fichier .csv ─────────────────> parse_csv ───┐
                                                 ├──> transactions validées ──> traitement
    fichier .png/.jpg ──> OCR ──> parse_image ───┘
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pipeline.loading.csv_loader import parse_csv
from pipeline.loading.image_loader import IMAGE_EXTENSIONS, is_image, parse_image
from pipeline.loading.ocr import run_ocr
from pipeline.models import Transaction

CSV_EXTENSION = ".csv"
SUPPORTED_EXTENSIONS: tuple[str, ...] = (CSV_EXTENSION, *IMAGE_EXTENSIONS)
SOURCE_TYPES = ("csv", "image")


def is_supported(name: str) -> bool:
    return name.lower().endswith(SUPPORTED_EXTENSIONS)


def source_type(name: str) -> str:
    """Type d'un fichier d'après son extension : « csv » ou « image »."""
    if is_image(name):
        return "image"
    if name.lower().endswith(CSV_EXTENSION):
        return "csv"
    raise ValueError(f"type de fichier non pris en charge : {name}")


def content_type(name: str) -> str:
    """Type MIME utilisé lors de l'envoi vers le stockage S3."""
    extension = Path(name).suffix.lower()
    return {
        ".csv": "text/csv",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(extension, "application/octet-stream")


def list_input_files(directory: Path) -> list[Path]:
    """Fichiers CSV et images d'un dossier, triés par nom."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {directory}")
    return sorted(
        path for path in directory.iterdir() if path.is_file() and is_supported(path.name)
    )


def parse_file(
    content: bytes, name: str, ocr: Callable[[bytes], str] = run_ocr
) -> list[Transaction]:
    """Charge un fichier, quel que soit son type, et retourne ses transactions validées."""
    if source_type(name) == "image":
        return parse_image(content, name, ocr)
    return parse_csv(content, name)
