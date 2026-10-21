"""Référentiel fixe de comptes bancaires utilisés pour générer les transactions.

Les comptes sont volontairement peu nombreux : chaque IBAN apparaît plusieurs fois,
ce qui rend les agrégations (sommes par IBAN, par banque) significatives et testables.
Deux banques possèdent deux comptes, pour distinguer la somme par IBAN de la somme par banque.
"""

from dataclasses import dataclass

from pipeline.generation.iban import build_iban


@dataclass(frozen=True)
class Account:
    iban: str
    pays: str  # code pays ISO 3166-1 alpha-2
    banque: str


ACCOUNTS: tuple[Account, ...] = (
    Account(build_iban("FR", "30004000031234567890143"), "FR", "BNP Paribas"),
    Account(build_iban("FR", "30004000039876543210187"), "FR", "BNP Paribas"),
    Account(build_iban("FR", "30003012340001122334455"), "FR", "Société Générale"),
    Account(build_iban("DE", "100700000123456789"), "DE", "Deutsche Bank"),
    Account(build_iban("DE", "100700000555666777"), "DE", "Deutsche Bank"),
    Account(build_iban("ES", "00490001500123456789"), "ES", "Banco Santander"),
    Account(build_iban("IT", "X0200801600000012345678"), "IT", "UniCredit"),
    Account(build_iban("BE", "310123456789"), "BE", "ING Belgique"),
)
