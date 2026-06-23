from __future__ import annotations

import logging
import re
from functools import lru_cache

import boto3
from botocore.client import Config
from fastapi import UploadFile

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_S3_CLIENT_CONFIG = Config(
    signature_version="s3v4",
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 2, "mode": "standard"},
)


def _require_s3_settings() -> Settings:
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("S3_ENDPOINT_URL", settings.S3_ENDPOINT_URL),
            ("S3_BUCKET_NAME", settings.S3_BUCKET_NAME),
            ("S3_ACCESS_KEY_ID", settings.S3_ACCESS_KEY_ID),
            ("S3_SECRET_ACCESS_KEY", settings.S3_SECRET_ACCESS_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Object storage is not configured. Set: " + ", ".join(missing)
        )
    return settings


@lru_cache
def get_s3_client():
    settings = _require_s3_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
        config=_S3_CLIENT_CONFIG,
    )


def sanitize_filename(filename: str) -> str:
    name = filename.split("/")[-1].split("\\")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "upload"


def build_object_key(
    group_id: str,
    meeting_id: str,
    file_type: str,
    original_filename: str,
) -> str:
    settings = get_settings()
    safe_name = sanitize_filename(original_filename)
    prefix = settings.S3_PREFIX.strip("/")
    return (
        f"{prefix}/groups/{group_id}/meetings/{meeting_id}/"
        f"{file_type.lower()}-{safe_name}"
    )


async def upload_meeting_file(
    *,
    group_id: str,
    meeting_id: str,
    file_type: str,
    upload_file: UploadFile,
) -> str:
    settings = _require_s3_settings()
    contents = await upload_file.read()
    if len(contents) > settings.MEETING_FILE_MAX_BYTES:
        raise ValueError(
            f"{file_type} file exceeds max size of "
            f"{settings.MEETING_FILE_MAX_BYTES} bytes"
        )

    key = build_object_key(
        group_id,
        meeting_id,
        file_type,
        upload_file.filename or "upload",
    )

    get_s3_client().put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=key,
        Body=contents,
        ContentType=upload_file.content_type or "application/octet-stream",
    )
    return key


def download_file(key: str) -> bytes:
    settings = _require_s3_settings()
    response = get_s3_client().get_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=key,
    )
    return response["Body"].read()


def _s3_is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.S3_ENDPOINT_URL
        and settings.S3_BUCKET_NAME
        and settings.S3_ACCESS_KEY_ID
        and settings.S3_SECRET_ACCESS_KEY
    )


def delete_file(key: str) -> None:
    delete_files([key])


def delete_files(keys: list[str]) -> None:
    """Best-effort object storage cleanup. Never raises — delete must not hang."""
    if not keys or not _s3_is_configured():
        return

    try:
        settings = get_settings()
        client = get_s3_client()
        client.delete_objects(
            Bucket=settings.S3_BUCKET_NAME,
            Delete={
                "Objects": [{"Key": key} for key in keys],
                "Quiet": True,
            },
        )
    except Exception as exc:
        logger.warning(
            "Could not delete %d object(s) from storage: %s",
            len(keys),
            exc,
        )
