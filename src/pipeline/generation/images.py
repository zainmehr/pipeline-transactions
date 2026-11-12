"""Génération déterministe d'images de transactions (relevés d'opérations « scannés »).

Chaque image représente un relevé de quelques transactions, présentées comme sur un
document bancaire : IBAN groupés par 4 caractères, date au format français, montant
avec séparateur de milliers et virgule décimale. Ces différences avec le format CSV
obligent l'étape d'extraction (OCR puis transformation) à normaliser les valeurs.

Comme pour les CSV, une même graine produit toujours exactement les mêmes images : la
police est embarquée dans le paquet pour que le rendu soit identique sur toutes les
machines. La mise en forme du texte (pure) est séparée du rendu et de l'écriture (I/O).
"""

from __future__ import annotations

import argparse
import io
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from importlib.resources import as_file, files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline.generation.generator import DEFAULT_SEED, EDGE_CASE_AMOUNTS, generate_batch
from pipeline.models import Transaction

# Les images utilisent des lots distincts de ceux des CSV : leur contenu est différent.
IMAGE_BATCH_OFFSET = 1_000
TRANSACTIONS_PER_IMAGE = 3
DEFAULT_NB_IMAGES = 2

FONT_SIZE = 26
LINE_HEIGHT = 38
MARGIN = 40
IMAGE_WIDTH = 1100

TITLE = "RELEVÉ D'OPÉRATIONS"
COUNT_LABEL = "Nombre de transactions"

# Libellés des champs, dans l'ordre d'affichage, et colonne CSV correspondante.
FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("Date", "datetime_transaction"),
    ("IBAN émetteur", "iban_origine"),
    ("Pays émetteur", "pays_source"),
    ("Banque émettrice", "banque_source"),
    ("IBAN destinataire", "iban_destinataire"),
    ("Pays destinataire", "pays_destinataire"),
    ("Montant", "montant"),
)


# --- Mise en forme (pure) ----------------------------------------------------


def format_iban(iban: str) -> str:
    """IBAN au format papier : groupes de 4 caractères (FR76 3000 ...)."""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def format_amount(amount: Decimal) -> str:
    """Montant au format français : 12 345,67 EUR."""
    integer, cents = f"{amount:.2f}".split(".")
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    return f"{' '.join(groups)},{cents} EUR"


def format_fields(transaction: Transaction) -> dict[str, str]:
    """Valeurs affichées sur le relevé pour une transaction, par colonne CSV."""
    return {
        "datetime_transaction": transaction.datetime_transaction.strftime("%d/%m/%Y %H:%M:%S"),
        "iban_origine": format_iban(transaction.iban_origine),
        "pays_source": transaction.pays_source,
        "banque_source": transaction.banque_source,
        "iban_destinataire": format_iban(transaction.iban_destinataire),
        "pays_destinataire": transaction.pays_destinataire,
        "montant": format_amount(transaction.montant),
    }


def statement_lines(transactions: list[Transaction], reference: str) -> list[str]:
    """Lignes de texte du relevé, telles qu'elles apparaissent sur l'image."""
    lines = [TITLE, f"Référence : {reference}", f"{COUNT_LABEL} : {len(transactions)}", ""]
    for number, transaction in enumerate(transactions, start=1):
        values = format_fields(transaction)
        lines.append(f"Transaction {number}")
        lines.extend(f"{label} : {values[column]}" for label, column in FIELD_LABELS)
        lines.append("")
    return lines


def image_transactions(seed: int, image_index: int) -> list[Transaction]:
    """Transactions de l'image numéro `image_index`. Même (seed, index) => même résultat.

    La première image contient les cas limites du flag (4999,99 / 5000,00 / 5000,01),
    pour vérifier que le seuil est correctement appliqué après l'OCR.
    """
    batch = generate_batch(seed, IMAGE_BATCH_OFFSET + image_index)[:TRANSACTIONS_PER_IMAGE]
    # Les dates sont ramenées à la même période que celles des CSV.
    shift = timedelta(days=IMAGE_BATCH_OFFSET)
    transactions = [
        replace(tx, datetime_transaction=tx.datetime_transaction - shift) for tx in batch
    ]
    if image_index == 0:
        transactions = [
            replace(tx, montant=amount)
            for tx, amount in zip(transactions, EDGE_CASE_AMOUNTS, strict=True)
        ]
    return transactions


# --- Rendu et écriture (I/O) -------------------------------------------------


def _font() -> ImageFont.FreeTypeFont:
    resource = files("pipeline.generation").joinpath("fonts/DejaVuSansMono.ttf")
    with as_file(resource) as path:
        return ImageFont.truetype(str(path), FONT_SIZE)


def render_image(lines: list[str]) -> bytes:
    """Dessine les lignes en noir sur fond blanc et retourne l'image au format PNG."""
    image = Image.new("L", (IMAGE_WIDTH, 2 * MARGIN + LINE_HEIGHT * len(lines)), 255)
    draw = ImageDraw.Draw(image)
    font = _font()
    for index, line in enumerate(lines):
        draw.text((MARGIN, MARGIN + LINE_HEIGHT * index), line, font=font, fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_image_files(
    output_dir: Path, nb_images: int = DEFAULT_NB_IMAGES, seed: int = DEFAULT_SEED
) -> list[Path]:
    """Génère `nb_images` relevés au format PNG dans `output_dir`."""
    if nb_images < 1:
        raise ValueError("nb_images doit être supérieur ou égal à 1")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(nb_images):
        reference = f"REL-{seed}-{index + 1:03d}"
        lines = statement_lines(image_transactions(seed, index), reference)
        path = output_dir / f"releve_{index + 1:03d}.png"
        path.write_bytes(render_image(lines))
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère des relevés de transactions en PNG.")
    parser.add_argument("--output", type=Path, default=Path("data"), help="Dossier de sortie")
    parser.add_argument("--images", type=int, default=DEFAULT_NB_IMAGES, help="Nombre d'images")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Graine aléatoire")
    args = parser.parse_args()

    for path in generate_image_files(args.output, args.images, args.seed):
        print(f"Image générée : {path}")


if __name__ == "__main__":
    main()
