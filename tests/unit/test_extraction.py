"""Extraction des transactions depuis le texte OCR (fonctions pures, sans Tesseract)."""

import copy

import pytest

from pipeline.extraction.statement import (
    StatementError,
    extract_rows,
    normalize_amount,
    normalize_country,
    normalize_datetime,
    normalize_iban,
    normalize_label,
)
from pipeline.generation.images import image_transactions, statement_lines
from pipeline.loading.image_loader import (
    InvalidImageError,
    parse_image,
    transactions_from_text,
)
from tests.factories import BNP_1, DEUTSCHE, make_transaction

RELEVE = """RELEVÉ D'OPÉRATIONS
Référence : REL-42-001
Nombre de transactions : 2

Transaction 1
Date : 01/01/2026 08:22:30
IBAN émetteur : FR76 3000 4000 0312 3456 7890 143
Pays émetteur : FR
Banque émettrice : BNP Paribas
IBAN destinataire : DE65 1007 0000 0123 4567 89
Pays destinataire : DE
Montant : 12 345,67 EUR

Transaction 2
Date : 02/01/2026 09:00:00
IBAN émetteur : DE65 1007 0000 0123 4567 89
Pays émetteur : DE
Banque émettrice : Deutsche Bank
IBAN destinataire : FR76 3000 4000 0312 3456 7890 143
Pays destinataire : FR
Montant : 5 000,01 EUR
"""


# --- Normalisation des valeurs -------------------------------------------------


@pytest.mark.parametrize(
    ("label", "attendu"),
    [
        ("IBAN émetteur", "ibanemetteur"),
        ("Iban  emetteur", "ibanemetteur"),
        ("Banque émettrice", "banqueemettrice"),
        ("Nombre de transactions", "nombredetransactions"),
    ],
)
def test_normalisation_des_libelles(label, attendu):
    assert normalize_label(label) == attendu


@pytest.mark.parametrize(
    ("lu", "attendu"),
    [
        ("FR76 3000 4000 0312 3456 7890 143", "FR7630004000031234567890143"),
        ("FRO8 3000 4000", "FR0830004000"),  # O lu à la place de 0 dans la clé
        ("fr76 3000", "FR763000"),
        ("F8I2 3000", "FB123000"),  # chiffres lus à la place de lettres dans le pays
        ("IT65 X020 0801", "IT65X0200801"),  # lettre légitime du BBAN conservée
    ],
)
def test_normalisation_des_iban(lu, attendu):
    assert normalize_iban(lu) == attendu


@pytest.mark.parametrize(
    ("lu", "attendu"),
    [
        ("12 345,67 EUR", "12345.67"),
        ("5 000,00 €", "5000.00"),
        ("4 999,99 EUR", "4999.99"),
        ("1.234,50 EUR", "1234.50"),
        ("1O0,00 EUR", "100.00"),  # O lu à la place de 0
        ("illisible", "illisible"),
    ],
)
def test_normalisation_des_montants(lu, attendu):
    assert normalize_amount(lu) == attendu


@pytest.mark.parametrize(
    ("lu", "attendu"),
    [
        ("01/01/2026 08:22:30", "2026-01-01T08:22:30"),
        ("01/01/2026 08 : 22 : 30", "2026-01-01T08:22:30"),
        ("O1/01/2026 08:22", "2026-01-01T08:22:00"),
        ("31/02/2026 08:00:00", "31/02/2026 08:00:00"),  # date impossible : inchangée
    ],
)
def test_normalisation_des_dates(lu, attendu):
    assert normalize_datetime(lu) == attendu


def test_normalisation_des_pays():
    assert normalize_country(" fr ") == "FR"
    assert normalize_country("8E") == "BE"


# --- Extraction ----------------------------------------------------------------


def test_extraction_d_un_releve():
    rows = extract_rows(RELEVE)

    assert len(rows) == 2
    assert rows[0] == {
        "datetime_transaction": "2026-01-01T08:22:30",
        "iban_origine": "FR7630004000031234567890143",
        "pays_source": "FR",
        "banque_source": "BNP Paribas",
        "iban_destinataire": "DE65100700000123456789",
        "pays_destinataire": "DE",
        "montant": "12345.67",
    }
    assert rows[1]["montant"] == "5000.01"


def test_extraction_sans_en_tete_de_transaction():
    """Sans « Transaction N », la répétition d'un champ démarre une nouvelle transaction."""
    sans_en_tete = "\n".join(
        line for line in RELEVE.splitlines() if not line.startswith("Transaction")
    )
    assert extract_rows(sans_en_tete) == extract_rows(RELEVE)


def test_champ_illisible_signale():
    texte = RELEVE.replace("Montant : 12 345,67 EUR", "Mntnt 12 345,67 EUR")
    with pytest.raises(StatementError) as erreur:
        extract_rows(texte)
    assert erreur.value.errors == ["transaction 1 : champs illisibles : montant"]


def test_transaction_manquante_detectee_grace_au_nombre_annonce():
    texte = RELEVE.replace("Nombre de transactions : 2", "Nombre de transactions : 3")
    with pytest.raises(StatementError, match="2 transaction\\(s\\) lue\\(s\\) alors que le relevé"):
        extract_rows(texte)


def test_texte_sans_transaction():
    with pytest.raises(StatementError, match="aucune transaction reconnue"):
        extract_rows("Une photo de vacances\nsans aucun relevé")


def test_extraction_pure():
    texte = copy.copy(RELEVE)
    premier = extract_rows(texte)
    assert texte == RELEVE
    assert extract_rows(texte) == premier


# --- Extraction puis validation (même validation que les CSV) ------------------


def test_transactions_validees_comme_un_csv():
    transactions = transactions_from_text(RELEVE, "releve.png")
    assert transactions[0] == make_transaction(
        origin=BNP_1,
        destination=DEUTSCHE,
        montant="12345.67",
        datetime_transaction=transactions[0].datetime_transaction,
    )
    assert str(transactions[0].datetime_transaction) == "2026-01-01 08:22:30"


def test_iban_mal_lu_rejete_par_la_cle_de_controle():
    # Un 7 lu comme un 1 : le format est correct, mais la clé modulo 97 ne l'est plus.
    texte = RELEVE.replace("FR76 3000 4000 0312 3456 7890 143", "FR76 3000 4000 0312 3456 1890 143")
    with pytest.raises(InvalidImageError) as erreur:
        transactions_from_text(texte, "releve.png")
    message = str(erreur.value)
    assert message.startswith("Image invalide : releve.png (2 erreur(s))")
    assert "transaction 1 : iban_origine invalide" in message
    assert "transaction 2 : iban_destinataire invalide" in message


def test_caractere_accentue_dans_un_iban_rejete_proprement():
    texte = RELEVE.replace("FR76 3000 4000 0312 3456 7890 143", "FR76 3000 4000 0312 3456 789É 143")
    with pytest.raises(InvalidImageError, match="iban_origine invalide"):
        transactions_from_text(texte, "releve.png")


def test_parse_image_avec_ocr_simule():
    """Aller-retour génération -> texte -> extraction, sans Tesseract."""
    attendues = image_transactions(42, 0)
    texte = "\n".join(statement_lines(attendues, "REL-42-001"))
    assert parse_image(b"image", "releve_001.png", ocr=lambda _: texte) == attendues


def test_erreur_de_lecture_ocr_convertie_en_image_invalide():
    from pipeline.loading.ocr import OCRError

    def ocr_en_echec(_content):
        raise OCRError("Tesseract n'a pas pu lire l'image : format inconnu")

    with pytest.raises(InvalidImageError, match="format inconnu"):
        parse_image(b"pas une image", "abime.png", ocr=ocr_en_echec)
