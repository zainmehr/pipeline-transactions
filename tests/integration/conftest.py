"""Fixtures des tests d'intégration (PostgreSQL et stockage S3 réels).

Les tests utilisent la base TEST_DB_NAME (par défaut « pipeline_test »), vidée avant
chaque test, et un bucket S3 temporaire propre à chaque test. Si un service est
inaccessible, ses tests sont ignorés en local, mais échouent lorsque REQUIRE_DB=1 ou
REQUIRE_S3=1 (cas de la CI), pour qu'un problème ne passe jamais inaperçu.
"""

import os
import uuid
from dataclasses import replace

import psycopg
import pytest
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from pipeline.storage.database import DatabaseConfig, apply_schema, connect
from pipeline.storage.s3 import S3Config, create_client

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


@pytest.fixture(scope="session")
def s3_base_config():
    load_dotenv()
    config = S3Config.from_env()
    try:
        create_client(config, timeout=3).list_buckets()
    except (BotoCoreError, ClientError) as error:
        message = f"Stockage S3 inaccessible ({config.endpoint_url}) : {error}"
        if os.environ.get("REQUIRE_S3") == "1":
            pytest.fail(message)
        pytest.skip(f"{message} — lancez `docker compose up -d`")
    return config


@pytest.fixture
def s3(s3_base_config, monkeypatch):
    """Bucket S3 temporaire, unique pour chaque test et supprimé ensuite.
    Les variables d'environnement pointent vers lui, comme en ligne de commande."""
    config = replace(s3_base_config, bucket=f"test-{uuid.uuid4().hex[:12]}")
    monkeypatch.setenv("S3_BUCKET", config.bucket)
    client = create_client(config)
    yield config, client
    try:
        objects = client.list_objects_v2(Bucket=config.bucket).get("Contents", [])
        for obj in objects:
            client.delete_object(Bucket=config.bucket, Key=obj["Key"])
        client.delete_bucket(Bucket=config.bucket)
    except ClientError:
        pass  # le bucket n'a pas été créé par le test
