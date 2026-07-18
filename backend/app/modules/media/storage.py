from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import boto3

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class StoredObjectMetadata:
    content_type: str
    size_bytes: int
    checksum_sha256: str | None


@dataclass(frozen=True, slots=True)
class StoredObject:
    content: bytes
    content_type: str
    size_bytes: int
    checksum_sha256: str
    service_checksum_sha256: str | None


class MediaStorage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_media_bucket
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
        )

    def create_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str | None,
        expires_seconds: int,
    ) -> str:
        params = {
            "Bucket": self._bucket,
            "Key": object_key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
        }
        if checksum_sha256 is not None:
            params["ChecksumSHA256"] = base64.b64encode(bytes.fromhex(checksum_sha256)).decode()
        return str(
            self._client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expires_seconds,
                HttpMethod="PUT",
            )
        )

    async def head(self, object_key: str) -> StoredObjectMetadata:
        response = await asyncio.to_thread(
            self._client.head_object,
            Bucket=self._bucket,
            Key=object_key,
            ChecksumMode="ENABLED",
        )
        return StoredObjectMetadata(
            content_type=str(response.get("ContentType", "")),
            size_bytes=int(response.get("ContentLength", -1)),
            checksum_sha256=str(response["ChecksumSHA256"])
            if response.get("ChecksumSHA256")
            else None,
        )

    async def download_quarantine(self, object_key: str, *, max_bytes: int) -> StoredObject:
        if not object_key.startswith("quarantine/listings/"):
            raise ValueError("invalid quarantine object prefix")
        return await asyncio.to_thread(self._download_quarantine, object_key, max_bytes)

    def _download_quarantine(self, object_key: str, max_bytes: int) -> StoredObject:
        response: dict[str, Any] = self._client.get_object(
            Bucket=self._bucket,
            Key=object_key,
            ChecksumMode="ENABLED",
        )
        body = response["Body"]
        try:
            content = bytes(body.read(max_bytes + 1))
        finally:
            body.close()
        return StoredObject(
            content=content,
            content_type=str(response.get("ContentType", "")),
            size_bytes=int(response.get("ContentLength", -1)),
            checksum_sha256=sha256(content).hexdigest(),
            service_checksum_sha256=(
                str(response["ChecksumSHA256"]) if response.get("ChecksumSHA256") else None
            ),
        )

    async def put_private_derivative(
        self, *, object_key: str, content: bytes, content_type: str
    ) -> None:
        if not object_key.startswith("private/derivatives/listings/"):
            raise ValueError("invalid derivative object prefix")
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=object_key,
            Body=content,
            ContentType=content_type,
            CacheControl="private, max-age=31536000, immutable",
            ServerSideEncryption="AES256",
        )

    async def delete_private_derivatives(self, object_keys: list[str]) -> None:
        keys = [key for key in object_keys if key.startswith("private/derivatives/listings/")]
        if not keys:
            return
        await asyncio.to_thread(
            self._client.delete_objects,
            Bucket=self._bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )
