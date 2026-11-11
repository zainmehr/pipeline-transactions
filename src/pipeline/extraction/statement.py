"""Extraction des transactions depuis le texte d'un relevé lu par OCR.

Toutes les fonctions de ce module sont pures : elles reçoivent le texte produit par l'OCR
et le transforment en lignes au format CSV (mêmes colonnes, mêmes formats), qui sont
ensuite validées exactement comme une ligne d'un fichier CSV.

    texte OCR ──> découpage en blocs ──> libellé : valeur ──> normalisation ──> ligne CSV

La normalisation corrige les écarts de présentation (IBAN groupés par 4, date au format
français, montant « 12 345,67 EUR ») et les confusions classiques de l'OCR entre lettres et
chiffres, uniquement là où le format attendu est sans ambiguïté (ex. la clé d'un IBAN est
toujours composée de 2 chiffres). Une erreur de lecture plus profonde est détectée ensuite
par la validation, en particulier par la clé de contrôle modulo 97 des IBAN.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from pipeline.models import CSV_COLUMNS

# Libellé normalisé (minuscules, sans accent ni espace) -> colonne CSV.
LABELS: dict[str, str] = {
    "date": "datetime_transaction",
    "ibanemetteur": "iban_origine",
    "paysemetteur": "pays_source",
    "banqueemettrice": "banque_source",
    "ibandestinataire": "iban_destinataire",
    "paysdestinataire": "pays_destinataire",
    "montant": "montant",
}
COUNT_LABEL = "nombredetransactions"

TRANSACTION_HEADER = re.compile(r"^\s*transaction\s*n?°?\s*\d+\s*$", re.IGNORECASE)
DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")

# Confusions fréquentes de l'OCR, corrigées selon ce qui est attendu à cet endroit.
TO_DIGIT = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1", "S": "5"})
TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B"})


class StatementError(ValueError):
    """Le texte ne permet pas d'extraire les transactions ; contient la liste des erreurs."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(" ; ".join(errors))


@dataclass
class _Block:
    number: int
    values: dict[str, str] = field(default_factory=dict)


def normalize_label(label: str) -> str:
    """« IBAN émetteur » -> « ibanemetteur » : insensible aux accents, à la casse et aux espaces."""
    decomposed = unicodedata.normalize("NFKD", label)
    return "".join(char for char in decomposed if char.isalpha()).lower()


def normalize_iban(value: str) -> str:
    """Supprime les espaces et corrige les confusions dans le code pays et la clé."""
    iban = re.sub(r"\s+", "", value).upper()
    if len(iban) < 4:
        return iban
    return iban[:2].translate(TO_LETTER) + iban[2:4].translate(TO_DIGIT) + iban[4:]


def normalize_country(value: str) -> str:
    return re.sub(r"\s+", "", value).upper().translate(TO_LETTER)


def normalize_amount(value: str) -> str:
    """« 12 345,67 EUR » -> « 12345.67 ». Une valeur illisible est renvoyée telle quelle,
    pour que la validation produise un message d'erreur explicite."""
    text = re.sub(r"(?i)eur|€", "", value)
    text = re.sub(r"[\s  ]", "", text).translate(TO_DIGIT)
    if not re.fullmatch(r"[\d.,]+", text):
        return value.strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return text


def normalize_datetime(value: str) -> str:
    """« 01/01/2026 08:22:30 » -> « 2026-01-01T08:22:30 » (ISO 8601)."""
    text = re.sub(r"\s+", " ", value.translate(TO_DIGIT)).strip()
    text = re.sub(r"\s*:\s*", ":", text)
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).isoformat()
        except ValueError:
            continue
    return value.strip()


NORMALIZERS = {
    "datetime_transaction": normalize_datetime,
    "iban_origine": normalize_iban,
    "pays_source": normalize_country,
    "banque_source": str.strip,
    "iban_destinataire": normalize_iban,
    "pays_destinataire": normalize_country,
    "montant": normalize_amount,
}


def _split_line(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    label, value = line.split(":", 1)
    return normalize_label(label), value.strip()


def _declared_count(value: str) -> int | None:
    digits = value.translate(TO_DIGIT).strip()
    return int(digits) if digits.isdigit() else None


def extract_rows(text: str) -> list[dict[str, str]]:
    """Extrait du texte OCR les transactions, sous forme de lignes au format CSV.

    Une transaction commence à un en-tête « Transaction N », ou à la répétition d'un champ
    déjà lu. Lève StatementError si une transaction est incomplète, si aucune transaction
    n'est trouvée, ou si leur nombre diffère de celui annoncé par le relevé.
    """
    blocks: list[_Block] = []
    declared: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if TRANSACTION_HEADER.match(line):
            blocks.append(_Block(len(blocks) + 1))
            continue
        parsed = _split_line(line)
        if parsed is None:
            continue
        label, value = parsed
        if label == COUNT_LABEL:
            declared = _declared_count(value)
            continue
        column = LABELS.get(label)
        if column is None:
            continue  # ligne sans intérêt (titre, référence...)
        if not blocks or column in blocks[-1].values:
            blocks.append(_Block(len(blocks) + 1))
        blocks[-1].values[column] = NORMALIZERS[column](value)

    errors: list[str] = []
    blocks = [block for block in blocks if block.values]
    if not blocks:
        errors.append("aucune transaction reconnue dans le texte extrait")
    for block in blocks:
        missing = [column for column in CSV_COLUMNS if not block.values.get(column)]
        if missing:
            errors.append(f"transaction {block.number} : champs illisibles : {', '.join(missing)}")
    if declared is not None and blocks and declared != len(blocks):
        errors.append(
            f"{len(blocks)} transaction(s) lue(s) alors que le relevé en annonce {declared}"
        )
    if errors:
        raise StatementError(errors)

    return [{column: block.values[column] for column in CSV_COLUMNS} for block in blocks]
