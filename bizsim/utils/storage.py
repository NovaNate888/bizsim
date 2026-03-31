"""
Cloudflare R2 storage helpers (S3-compatible via boto3).

Environment variables required:
  R2_ENDPOINT_URL      — https://<account_id>.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID     — R2 API token access key
  R2_SECRET_ACCESS_KEY — R2 API token secret key
  R2_BUCKET_NAME       — bucket name (default: bizsim)
"""
import io
import os


def _client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _bucket() -> str:
    return os.environ.get("R2_BUCKET_NAME", "bizsim")


def upload_fileobj(file_obj, key: str) -> None:
    """Upload a file-like object to R2 at the given key."""
    _client().upload_fileobj(file_obj, _bucket(), key)


def download_as_bytes(key: str) -> bytes:
    """Download an R2 object and return its contents as bytes."""
    buf = io.BytesIO()
    _client().download_fileobj(_bucket(), key, buf)
    return buf.getvalue()


def generate_presigned_url(key: str, expiry: int = 3600) -> str:
    """Return a presigned GET URL for the given R2 key."""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expiry,
    )
