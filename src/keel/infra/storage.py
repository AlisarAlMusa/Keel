"""Object storage (MinIO / S3-compatible) via boto3.

Stores catalog text (for RAG, later), model artifacts, and eval reports.
Created once at startup; bucket existence is ensured idempotently.
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def create_s3_client(
    *, endpoint: str, access_key: str, secret_key: str, region: str = "us-east-1"
) -> Any:
    """Create a boto3 S3 client pointed at the MinIO endpoint."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def ensure_bucket(client: Any, bucket: str) -> None:
    """Create the bucket if it does not already exist (idempotent)."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def put_text(client: Any, bucket: str, key: str, text: str) -> None:
    """Upload a UTF-8 text object."""
    client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))


def get_text(client: Any, bucket: str, key: str) -> str:
    """Download a UTF-8 text object."""
    resp = client.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read().decode("utf-8")  # type: ignore[no-any-return]


def delete_object(client: Any, bucket: str, key: str) -> None:
    """Delete an object; no-op if it does not exist."""
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except ClientError:
        pass
