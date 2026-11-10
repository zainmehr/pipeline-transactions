"""Fixtures des tests d'intégration (base PostgreSQL réelle).

Les tests utilisent la base TEST_DB_NAME (par défaut « pipeline_test »), vidée avant
chaque test. Si PostgreSQL est inaccessible, ils sont ignorés en local, mais échouent
lorsque REQUIRE_DB=1 (cas de la CI), pour qu'un problème ne passe jamais inaperçu.
"""

import os
from dataclasses import replace

import psycopg
import pytest
from dotenv import load_dotenv

from pipeline.storage.database import DatabaseConfig, apply_schema, connect

TABLES = (
    "total_sent_by_iban, total_sent_by_bank, total_received_by_iban, "
    "transactions, accounts, banks, processed_files"
)


@pytest.fixture(scope="session")
def db_config():
    load_dotenv()
    config = DatabaseConfig.from_env()
    return replace(config, dbname=os.environ.get("TEST_DB_NAME", "pipeline_test"))


@pytest.fixture(scope="session")
def db_session(db_config):
    try:
        conn = connect(db_config, timeout=3)
    except psycopg.OperationalError as error:
        message = f"PostgreSQL inaccessible ({db_config.describe()}) : {error}"
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(f"{message} — lancez `docker compose up -d`")
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def db(db_session):
    """Connexion à une base vide."""
    db_session.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    return db_session


@pytest.fixture
def pipeline_env(db, db_config, monkeypatch):
    """Base vide + variables d'environnement pointant vers la base de test,
    pour exécuter la pipeline complète comme en ligne de commande."""
    for var, value in {
        "DB_HOST": db_config.host,
        "DB_PORT": str(db_config.port),
        "DB_NAME": db_config.dbname,
        "DB_USER": db_config.user,
        "DB_PASSWORD": db_config.password,
    }.items():
        monkeypatch.setenv(var, value)
    return db
