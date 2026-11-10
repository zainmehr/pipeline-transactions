"""Accès au stockage objet compatible S3 (RustFS en local et en CI).

Les fichiers CSV générés sont envoyés dans un bucket, sous le préfixe `transactions/`.
Chaque objet porte en métadonnée l'empreinte SHA-256 de son contenu, ce qui permet de
vérifier après l'envoi que le stockage contient exactement le fichier envoyé.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

KEY_PREFIX = "transactions/"
HASH_METADATA = "sha256"


class TransientS3Error(Exception):
    """Erreur S3 temporaire (service saturé ou indisponible) : elle mérite un retry."""


# Erreurs réseau (service arrêté, connexion coupée, délai dépassé) et erreurs 5xx.
TRANSIENT_S3_ERRORS: tuple[type[BaseException], ...] = (
    BotoConnectionError,
    HTTPClientError,
    TransientS3Error,
)


@dataclass(frozen=True)
class S3Config:
    endpoint_url: str = "http://localhost:9000"
    access_key: str = "pipeline"
    secret_key: str = "pipeline-secret"
    bucket: str = "transactions"
    region: str = "us-east-1"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> S3Config:
        """Construit la configuration depuis S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_BUCKET, ..."""
        env = os.environ if env is None else env
        return cls(
            endpoint_url=env.get("S3_ENDPOINT_URL", cls.endpoint_url),
            access_key=env.get("S3_ACCESS_KEY", cls.access_key),
            secret_key=env.get("S3_SECRET_KEY", cls.secret_key),
            bucket=env.get("S3_BUCKET", cls.bucket),
            region=env.get("S3_REGION", cls.region),
        )

    def describe(self) -> str:
        """Description sans le secret, utilisable dans les messages d'erreur."""
        return f"{self.endpoint_url}/{self.bucket}"


def object_key(file_name: str) -> str:
    """Clé de l'objet S3 correspondant à un fichier CSV."""
    return f"{KEY_PREFIX}{file_name}"


def create_client(config: S3Config, timeout: int = 5):
    """Client S3. Les tentatives automatiques de boto3 sont désactivées : le retry est
    géré par la pipeline, pour qu'il soit visible et paramétrable."""
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def _raise_if_transient(error: ClientError) -> None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    code = error.response.get("Error", {}).get("Code", "")
    if status >= 500 or code in {"SlowDown", "ServiceUnavailable", "InternalError"}:
        raise TransientS3Error(f"{code or status} : {error}") from error


def ensure_bucket(client, bucket: str) -> bool:
    """Crée le bucket s'il n'existe pas. Retourne True s'il vient d'être créé."""
    try:
        client.head_bucket(Bucket=bucket)
        return False
    except ClientError as error:
        _raise_if_transient(error)
        code = error.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        _raise_if_transient(error)
        if error.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
            raise
        return False
    return True


def upload_file(client, bucket: str, path: Path, file_hash: str) -> str:
    """Envoie un fichier dans le bucket et retourne la clé de l'objet créé."""
    key = object_key(path.name)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=path.read_bytes(),
            ContentType="text/csv",
            Metadata={HASH_METADATA: file_hash},
        )
    except ClientError as error:
        _raise_if_transient(error)
        raise
    return key


def list_keys(client, bucket: str, prefix: str = KEY_PREFIX) -> list[str]:
    """Liste les clés des objets du bucket sous un préfixe, triées."""
    keys: list[str] = []
    try:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
    except ClientError as error:
        _raise_if_transient(error)
        raise
    return sorted(keys)


def object_hash(client, bucket: str, key: str) -> str | None:
    """Empreinte SHA-256 enregistrée sur l'objet, ou None si l'objet n'existe pas."""
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        _raise_if_transient(error)
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return response.get("Metadata", {}).get(HASH_METADATA)


def main() -> int:
    """Commande `list-s3` : affiche les fichiers présents dans le bucket."""
    import sys

    from botocore.exceptions import BotoCoreError
    from dotenv import load_dotenv

    load_dotenv()
    config = S3Config.from_env()
    try:
        keys = list_keys(create_client(config), config.bucket)
    except (BotoCoreError, ClientError, TransientS3Error) as error:
        print(f"ERREUR : stockage S3 inaccessible ({config.describe()}) : {error}", file=sys.stderr)
        return 1
    for key in keys:
        print(f"s3://{config.bucket}/{key}")
    print(f"{len(keys)} fichier(s) dans {config.describe()}")
    return 0
