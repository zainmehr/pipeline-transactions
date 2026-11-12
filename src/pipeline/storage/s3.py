"""Accès au stockage objet compatible S3 (RustFS en local et en CI).

Les fichiers (CSV et images) sont envoyés dans un bucket sous la clé
`transactions/<16 premiers caractères de l'empreinte SHA-256>/<nom du fichier>`.
La clé dépend du contenu : renvoyer le même fichier ne crée pas de doublon, et un fichier
de même nom mais de contenu différent n'écrase jamais le précédent, même s'il n'a pas
encore été traité. Chaque objet porte aussi son empreinte complète en métadonnée, ce qui
permet de vérifier son intégrité et d'ignorer un fichier déjà traité sans le télécharger.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

KEY_PREFIX = "transactions/"
HASH_METADATA = "sha256"


class TransientS3Error(Exception):
    """Erreur S3 temporaire (service saturé ou indisponible) : elle mérite un retry."""


class ObjectNotFoundError(Exception):
    """L'objet demandé n'existe pas (ou plus) dans le bucket."""


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


def object_key(file_name: str, file_hash: str) -> str:
    """Clé de l'objet S3 correspondant à un fichier CSV, dérivée de son contenu."""
    return f"{KEY_PREFIX}{file_hash[:16]}/{file_name}"


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


def upload_file(
    client,
    bucket: str,
    file_name: str,
    content: bytes,
    file_hash: str,
    content_type: str = "text/csv",
) -> str:
    """Envoie le contenu d'un fichier dans le bucket et retourne la clé de l'objet créé."""
    key = object_key(file_name, file_hash)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            Metadata={HASH_METADATA: file_hash},
        )
    except ClientError as error:
        _raise_if_transient(error)
        raise
    return key


def download_object(client, bucket: str, key: str) -> bytes:
    """Télécharge le contenu d'un objet du bucket."""
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except ClientError as error:
        _raise_if_transient(error)
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise ObjectNotFoundError(f"objet introuvable : s3://{bucket}/{key}") from error
        raise


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
