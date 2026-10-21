"""Construction et validation d'IBAN selon la norme ISO 13616 (clé modulo 97)."""


def _to_digits(text: str) -> str:
    """Convertit chaque caractère en chiffres : 0-9 inchangés, A=10, B=11, ..., Z=35."""
    return "".join(str(int(char, 36)) for char in text)


def compute_check_digits(country_code: str, bban: str) -> str:
    """Calcule les deux chiffres de contrôle d'un IBAN."""
    rearranged = bban.upper() + country_code.upper() + "00"
    return f"{98 - int(_to_digits(rearranged)) % 97:02d}"


def build_iban(country_code: str, bban: str) -> str:
    """Construit un IBAN valide à partir d'un code pays et d'un BBAN."""
    country_code, bban = country_code.upper(), bban.upper()
    return f"{country_code}{compute_check_digits(country_code, bban)}{bban}"


def is_valid_iban(iban: str) -> bool:
    """Vérifie le format et la clé de contrôle d'un IBAN."""
    iban = iban.replace(" ", "").upper()
    if not 15 <= len(iban) <= 34 or not iban.isalnum() or not iban[:2].isalpha():
        return False
    rearranged = iban[4:] + iban[:4]
    return int(_to_digits(rearranged)) % 97 == 1
