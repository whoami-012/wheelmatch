from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, MediaScannerProvider, Settings
from app.core.ids import uuid7
from app.modules.media.models import ListingMedia
from app.modules.media.processing import ImageRejectedError, ImageSanitizer, validate_source_object
from app.modules.media.scanner import (
    DeterministicMalwareScanner,
    ScannerPermanentError,
    ScannerTransientError,
    ScanStatus,
)
from app.modules.media.storage import StoredObject
from app.modules.media.worker import MediaProcessingRequested, processing_request_is_current


def jpeg_with_orientation_and_metadata() -> bytes:
    image = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[270] = "private description"
    exif[274] = 6
    output = BytesIO()
    image.save(output, format="JPEG", exif=exif, comment=b"private comment")
    image.close()
    return output.getvalue()


def stored_image(content: bytes, content_type: str = "image/jpeg") -> StoredObject:
    checksum = sha256(content).hexdigest()
    return StoredObject(
        content=content,
        content_type=content_type,
        size_bytes=len(content),
        checksum_sha256=checksum,
        service_checksum_sha256=base64.b64encode(bytes.fromhex(checksum)).decode(),
    )


def sanitizer(*, max_pixels: int = 1_000_000) -> ImageSanitizer:
    return ImageSanitizer(
        max_pixels=max_pixels,
        max_dimension=2_000,
        max_output_dimension=640,
    )


def test_valid_image_is_oriented_resized_reencoded_and_metadata_free() -> None:
    stored = stored_image(jpeg_with_orientation_and_metadata())
    source = validate_source_object(
        stored,
        expected_content_type="image/jpeg",
        expected_size_bytes=stored.size_bytes,
        expected_checksum_sha256=stored.checksum_sha256,
        max_input_bytes=1_000_000,
    )

    result = sanitizer().sanitize(source)

    assert {item.kind for item in result.derivatives} == {"thumbnail", "medium", "large"}
    assert len(result.perceptual_hash) == 16
    assert result.sanitized_checksum_sha256
    for derivative in result.derivatives:
        assert derivative.content.startswith(b"\xff\xd8\xff")
        assert derivative.width < derivative.height
        assert max(derivative.width, derivative.height) <= 640
        with Image.open(BytesIO(derivative.content)) as decoded:
            assert decoded.format == "JPEG"
            assert len(decoded.getexif()) == 0
            assert not ({"exif", "xmp", "comment", "icc_profile"} & decoded.info.keys())


def test_malformed_image_and_decompression_limits_fail_safely() -> None:
    malformed = stored_image(b"\xff\xd8\xffnot-an-image")
    malformed_source = validate_source_object(
        malformed,
        expected_content_type="image/jpeg",
        expected_size_bytes=malformed.size_bytes,
        expected_checksum_sha256=malformed.checksum_sha256,
        max_input_bytes=1_000_000,
    )
    with pytest.raises(ImageRejectedError, match="MALFORMED_IMAGE"):
        sanitizer().sanitize(malformed_source)

    image = Image.new("RGB", (100, 100), "blue")
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    bomb = stored_image(output.getvalue(), "image/png")
    bomb_source = validate_source_object(
        bomb,
        expected_content_type="image/png",
        expected_size_bytes=bomb.size_bytes,
        expected_checksum_sha256=bomb.checksum_sha256,
        max_input_bytes=1_000_000,
    )
    with pytest.raises(ImageRejectedError, match="IMAGE_DECOMPRESSION_BOMB"):
        sanitizer(max_pixels=1_000).sanitize(bomb_source)


def test_processing_version_and_stale_event_rules_are_deterministic() -> None:
    media_id = uuid7()
    listing_id = uuid7()
    media = ListingMedia(
        id=media_id,
        listing_id=listing_id,
        created_by_user_id=uuid7(),
        object_key=f"quarantine/listings/{listing_id}/{media_id}",
        expected_content_type="image/jpeg",
        expected_size_bytes=100,
        sort_order=0,
        status="processing",
        processing_version=2,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    current = MediaProcessingRequested(
        media_id=media_id, listing_id=listing_id, processing_version=2
    )
    stale = current.model_copy(update={"processing_version": 1})

    assert processing_request_is_current(media, current)
    assert not processing_request_is_current(media, stale)
    media.status = "moderation_pending"
    assert not processing_request_is_current(media, current)
    with pytest.raises(ValidationError):
        MediaProcessingRequested.model_validate(
            {**current.model_dump(), "object_key": "caller-controlled"}
        )


@pytest.mark.asyncio
async def test_scanner_rejection_and_failure_classes_are_explicit() -> None:
    rejected = DeterministicMalwareScanner(result=ScanStatus.REJECTED)
    assert (await rejected.scan(b"image")).status is ScanStatus.REJECTED

    transient = DeterministicMalwareScanner(error=ScannerTransientError())
    with pytest.raises(ScannerTransientError):
        await transient.scan(b"image")

    permanent = DeterministicMalwareScanner(error=ScannerPermanentError())
    with pytest.raises(ScannerPermanentError):
        await permanent.scan(b"image")


def test_non_development_configuration_rejects_noop_scanner() -> None:
    with pytest.raises(ValueError, match="production-grade media malware scanner"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            database_url=SecretStr("postgresql+asyncpg://service@db.internal/wheelmatch"),
            redis_url=SecretStr("redis://cache.internal:6379/0"),
            aws_endpoint_url=None,
            secret_hash_key=SecretStr("configured-secret-hash-key"),
            access_token_signing_key=SecretStr("configured-access-token-key"),
            media_scanner_provider=MediaScannerProvider.DISABLED,
        )
