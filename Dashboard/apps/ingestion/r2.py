"""
R2 storage helper — thin wrapper around boto3's S3-compatible client.

Cloudflare R2 is S3-compatible. Configure via settings:
    R2_ACCOUNT_ID         — Cloudflare account ID
    R2_ACCESS_KEY_ID      — R2 API token access key
    R2_SECRET_ACCESS_KEY  — R2 API token secret key
    R2_BUCKET_NAME        — bucket name

Key conventions:
    uploads/<job_pk>/<original_filename>   — uploaded CSVs (permanent backup)
    archive/sensor_<serial>/<YYYY>/<date>.jsonl.gz  — archived raw readings
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _client():
    import boto3
    from django.conf import settings

    endpoint = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def _bucket():
    from django.conf import settings
    return settings.R2_BUCKET_NAME


def upload_fileobj(fileobj, key: str, content_type: str = "text/csv") -> None:
    """Upload an open file-like object to R2."""
    _client().upload_fileobj(
        fileobj, _bucket(), key,
        ExtraArgs={"ContentType": content_type},
    )
    logger.info("R2 upload: %s", key)


def upload_path(local_path: Path, key: str, content_type: str = "text/csv") -> None:
    """Upload a local file to R2."""
    _client().upload_file(str(local_path), _bucket(), key, ExtraArgs={"ContentType": content_type})
    logger.info("R2 upload: %s", key)


def download_to_path(key: str, dest: Path) -> None:
    """Download an R2 object to a local file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _client().download_file(_bucket(), key, str(dest))
    logger.info("R2 download: %s → %s", key, dest)


def delete(key: str) -> None:
    """Delete a single object from R2."""
    _client().delete_object(Bucket=_bucket(), Key=key)
    logger.info("R2 delete: %s", key)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload raw bytes to R2."""
    _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)
    logger.info("R2 put_bytes: %s (%d bytes)", key, len(data))
