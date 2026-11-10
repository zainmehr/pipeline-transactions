"""Accès à la base PostgreSQL (entrées/sorties uniquement).

L'insertion d'un fichier est atomique : le fichier, ses transactions et la mise à jour
des sommes sont écrits dans une seule transaction SQL. En cas d'erreur, rien n'est écrit.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files

import psycopg

from pipeline.models import ProcessingResult

FILE_HASH_CONSTRAINT = "processed_files_file_hash_key"


class DuplicateFileError(Exception):
    """Le fichier a déjà été inséré en base (même contenu, donc même hash)."""


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    dbname: str = "pipeline"
    user: str = "pipeline"
    password: str = "pipeline"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabaseConfig:
        """Construit la configuration depuis les variables DB_HOST, DB_PORT, DB_NAME, ..."""
        env = os.environ if env is None else env
        return cls(
            host=env.get("DB_HOST", cls.host),
            port=int(env.get("DB_PORT", cls.port)),
            dbname=env.get("DB_NAME", cls.dbname),
            user=env.get("DB_USER", cls.user),
            password=env.get("DB_PASSWORD", cls.password),
        )

    def describe(self) -> str:
        """Description sans le mot de passe, utilisable dans les messages d'erreur."""
        return f"{self.user}@{self.host}:{self.port}/{self.dbname}"


def connect(config: DatabaseConfig, timeout: int = 5) -> psycopg.Connection:
    """Ouvre une connexion en autocommit : chaque bloc `conn.transaction()` est une vraie
    transaction SQL (BEGIN ... COMMIT), validée ou annulée en entier."""
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=config.user,
        password=config.password,
        connect_timeout=timeout,
        autocommit=True,
    )


def load_schema() -> str:
    """Retourne le script SQL de création du schéma (embarqué dans le paquet)."""
    return files("pipeline.storage").joinpath("schema.sql").read_text(encoding="utf-8")


def apply_schema(conn: psycopg.Connection) -> None:
    """Crée les tables et les vues si elles n'existent pas."""
    with conn.transaction():
        conn.execute(load_schema())


def fetch_processed_hashes(conn: psycopg.Connection) -> set[str]:
    """Retourne les hash de tous les fichiers déjà insérés."""
    return {row[0] for row in conn.execute("SELECT file_hash FROM processed_files")}


def insert_processed_file(
    conn: psycopg.Connection, file_name: str, file_hash: str, result: ProcessingResult
) -> int:
    """Insère un fichier traité et ses résultats. Retourne l'identifiant du fichier.

    Lève DuplicateFileError si un fichier de même contenu a déjà été inséré.
    """
    transactions = [ft.transaction for ft in result.transactions]
    accounts = sorted(
        {(tx.iban_origine, tx.pays_source) for tx in transactions}
        | {(tx.iban_destinataire, tx.pays_destinataire) for tx in transactions}
    )
    bank_names = sorted({tx.banque_source for tx in transactions})

    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO processed_files (file_name, file_hash, nb_transactions) "
                "VALUES (%s, %s, %s) RETURNING id",
                (file_name, file_hash, len(transactions)),
            )
            file_id = cur.fetchone()[0]

            cur.executemany(
                "INSERT INTO accounts (iban, pays) VALUES (%s, %s) ON CONFLICT (iban) DO NOTHING",
                accounts,
            )

            bank_ids: dict[str, int] = {}
            for name in bank_names:
                cur.execute(
                    "INSERT INTO banks (name) VALUES (%s) "
                    "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                    (name,),
                )
                bank_ids[name] = cur.fetchone()[0]

            cur.executemany(
                "INSERT INTO transactions (file_id, datetime_transaction, iban_origine, "
                "bank_source_id, iban_destinataire, montant, flag_montant_eleve) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        file_id,
                        ft.transaction.datetime_transaction,
                        ft.transaction.iban_origine,
                        bank_ids[ft.transaction.banque_source],
                        ft.transaction.iban_destinataire,
                        ft.transaction.montant,
                        ft.is_flagged,
                    )
                    for ft in result.transactions
                ],
            )

            # Les sommes du fichier s'ajoutent aux sommes déjà présentes en base.
            cur.executemany(
                "INSERT INTO total_sent_by_iban (iban, total) VALUES (%s, %s) "
                "ON CONFLICT (iban) DO UPDATE SET "
                "total = total_sent_by_iban.total + EXCLUDED.total, updated_at = now()",
                list(result.sent_by_iban.items()),
            )
            cur.executemany(
                "INSERT INTO total_sent_by_bank (bank_id, total) VALUES (%s, %s) "
                "ON CONFLICT (bank_id) DO UPDATE SET "
                "total = total_sent_by_bank.total + EXCLUDED.total, updated_at = now()",
                [(bank_ids[name], total) for name, total in result.sent_by_bank.items()],
            )
            cur.executemany(
                "INSERT INTO total_received_by_iban (iban, total) VALUES (%s, %s) "
                "ON CONFLICT (iban) DO UPDATE SET "
                "total = total_received_by_iban.total + EXCLUDED.total, updated_at = now()",
                list(result.received_by_iban.items()),
            )
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == FILE_HASH_CONSTRAINT:
            raise DuplicateFileError(f"Fichier déjà inséré en base : {file_name}") from error
        raise

    return file_id
