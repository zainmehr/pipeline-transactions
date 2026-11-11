"""Reconnaissance de caractères (OCR) avec Tesseract.

Seul module qui appelle le programme externe `tesseract` : il reçoit le contenu d'une image
et retourne le texte reconnu. L'extraction des transactions à partir de ce texte est faite
par des fonctions pures (pipeline.extraction.statement).
"""

from __future__ import annotations

import os
import shutil
import subprocess

DEFAULT_LANG = "fra"
# --psm 6 : la page est lue comme un bloc de texte uniforme, ligne par ligne.
PAGE_SEGMENTATION_MODE = "6"
TIMEOUT_SECONDS = 60


class OCRUnavailableError(Exception):
    """Tesseract (ou la langue demandée) n'est pas installé : aucune image ne peut être lue."""


class OCRError(Exception):
    """Tesseract n'a pas pu lire l'image (fichier corrompu, format non reconnu...)."""


def ocr_lang() -> str:
    return os.environ.get("OCR_LANG", DEFAULT_LANG)


def check_tesseract(lang: str | None = None) -> str:
    """Vérifie que Tesseract et la langue sont disponibles ; retourne la version."""
    lang = lang or ocr_lang()
    if shutil.which("tesseract") is None:
        raise OCRUnavailableError(
            "Tesseract est introuvable : installer le paquet tesseract-ocr "
            "(et tesseract-ocr-fra) pour traiter les images"
        )
    langs = subprocess.run(
        ["tesseract", "--list-langs"], capture_output=True, text=True, check=False
    ).stdout.split()
    if lang not in langs:
        raise OCRUnavailableError(
            f"langue OCR « {lang} » non installée pour Tesseract (paquet tesseract-ocr-{lang})"
        )
    version = subprocess.run(
        ["tesseract", "--version"], capture_output=True, text=True, check=False
    ).stdout
    return version.splitlines()[0] if version else "tesseract"


def run_ocr(content: bytes, lang: str | None = None) -> str:
    """Retourne le texte reconnu dans une image (PNG, JPEG, TIFF...)."""
    lang = lang or ocr_lang()
    try:
        completed = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", lang, "--psm", PAGE_SEGMENTATION_MODE],
            input=content,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise OCRUnavailableError(
            "Tesseract est introuvable : installer le paquet tesseract-ocr"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise OCRError(f"lecture OCR interrompue après {TIMEOUT_SECONDS} s") from error
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OCRError(f"Tesseract n'a pas pu lire l'image : {message[-1] if message else ''}")
    return completed.stdout.decode("utf-8", "replace")
