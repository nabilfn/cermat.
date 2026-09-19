"""File storage behind one interface.

Object keys are always server-generated (``<workspace>/<uuid><ext>``); the
original filename is metadata only and never touches a filesystem path.

LocalStorageProvider   development / single-host with a persistent volume
S3StorageProvider      any S3-compatible service (AWS S3, R2, MinIO, Spaces…)
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from fastapi.concurrency import run_in_threadpool

from app.config import settings


class StorageError(Exception):
    pass


class StorageProvider(Protocol):
    async def store(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def get_signed_url(self, key: str, *, filename: str, content_type: str, expires_seconds: int = 300) -> str | None: ...


def new_object_key(workspace_id: UUID, extension: str) -> str:
    safe_extension = extension if extension in {".pdf", ".png", ".jpg", ".jpeg", ".webp"} else ""
    return f"{workspace_id}/{uuid4().hex}{safe_extension}"


class LocalStorageProvider:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Defence in depth: keys are generated, but never resolve outside the root.
        if self.root not in path.parents:
            raise StorageError("Invalid object key.")
        return path

    async def store(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await run_in_threadpool(write)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("Stored file not found.")
        return await run_in_threadpool(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await run_in_threadpool(lambda: path.unlink(missing_ok=True))

    async def get_signed_url(self, key: str, *, filename: str, content_type: str, expires_seconds: int = 300) -> str | None:
        return None  # served through the authorised /documents/{id}/file endpoint


class S3StorageProvider:
    def __init__(self) -> None:
        import boto3

        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    async def store(self, key: str, data: bytes, content_type: str) -> str:
        await run_in_threadpool(
            self.client.put_object,
            Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type,
        )
        return key

    async def get(self, key: str) -> bytes:
        try:
            response = await run_in_threadpool(self.client.get_object, Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:  # noqa: BLE001
            raise StorageError("Stored file not found.") from exc
        return await run_in_threadpool(response["Body"].read)

    async def delete(self, key: str) -> None:
        await run_in_threadpool(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))

    async def get_signed_url(self, key: str, *, filename: str, content_type: str, expires_seconds: int = 300) -> str | None:
        return await run_in_threadpool(
            self.client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self._key(key),
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f'inline; filename="{filename}"',
            },
            ExpiresIn=expires_seconds,
        )


_provider: StorageProvider | None = None


def get_storage() -> StorageProvider:
    global _provider
    if _provider is None:
        _provider = S3StorageProvider() if settings.storage_provider == "s3" else LocalStorageProvider(settings.data_dir)
    return _provider
