from __future__ import annotations

import base64
import binascii
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.modules.media.storage import StoredObject

_SUPPORTED_SIGNATURES: dict[str, tuple[str, Callable[[bytes], bool]]] = {
    "JPEG": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "PNG": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "WEBP": (
        "image/webp",
        lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    ),
}


class ImageRejectedError(Exception):
    def __init__(self, failure_code: str) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class ValidatedImageSource:
    content: bytes
    checksum_sha256: str
    content_type: str
    source_format: str


@dataclass(frozen=True, slots=True)
class SanitizedDerivative:
    kind: str
    content: bytes
    content_type: str
    width: int
    height: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class SanitizedImageSet:
    derivatives: tuple[SanitizedDerivative, ...]
    source_width: int
    source_height: int
    sanitized_checksum_sha256: str
    perceptual_hash: str


def validate_source_object(
    stored: StoredObject,
    *,
    expected_content_type: str,
    expected_size_bytes: int,
    expected_checksum_sha256: str | None,
    max_input_bytes: int,
) -> ValidatedImageSource:
    if (
        stored.size_bytes != expected_size_bytes
        or len(stored.content) != expected_size_bytes
        or len(stored.content) > max_input_bytes
    ):
        raise ImageRejectedError("OBJECT_SIZE_MISMATCH")
    if stored.content_type != expected_content_type:
        raise ImageRejectedError("OBJECT_MIME_MISMATCH")
    if expected_checksum_sha256 is not None and stored.checksum_sha256 != expected_checksum_sha256:
        raise ImageRejectedError("OBJECT_CHECKSUM_MISMATCH")
    service_checksum = _decode_service_checksum(stored.service_checksum_sha256)
    if service_checksum is not None and service_checksum != stored.checksum_sha256:
        raise ImageRejectedError("OBJECT_CHECKSUM_MISMATCH")
    detected = next(
        (
            (source_format, content_type)
            for source_format, (content_type, matches) in _SUPPORTED_SIGNATURES.items()
            if matches(stored.content)
        ),
        None,
    )
    if detected is None:
        raise ImageRejectedError("UNSUPPORTED_FILE_SIGNATURE")
    source_format, detected_content_type = detected
    if detected_content_type != expected_content_type:
        raise ImageRejectedError("OBJECT_MIME_MISMATCH")
    return ValidatedImageSource(
        content=stored.content,
        checksum_sha256=stored.checksum_sha256,
        content_type=detected_content_type,
        source_format=source_format,
    )


class ImageSanitizer:
    def __init__(
        self,
        *,
        max_pixels: int,
        max_dimension: int,
        max_output_dimension: int,
    ) -> None:
        self._max_pixels = max_pixels
        self._max_dimension = max_dimension
        self._derivative_sizes = (
            ("thumbnail", 320),
            ("medium", min(960, max_output_dimension)),
            ("large", max_output_dimension),
        )

    def sanitize(self, source: ValidatedImageSource) -> SanitizedImageSet:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(source.content)) as probe:
                    self._validate_image(probe, source)
                    source_width, source_height = probe.size
                    probe.verify()
                with Image.open(BytesIO(source.content)) as decoded:
                    self._validate_image(decoded, source)
                    decoded.load()
                    oriented = ImageOps.exif_transpose(decoded)
                    clean = self._metadata_free_rgb(oriented)
        except ImageRejectedError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageRejectedError("IMAGE_DECOMPRESSION_BOMB") from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise ImageRejectedError("MALFORMED_IMAGE") from exc

        try:
            derivatives = tuple(
                self._encode_derivative(clean, kind=kind, max_dimension=max_dimension)
                for kind, max_dimension in self._derivative_sizes
            )
            large = next(item for item in derivatives if item.kind == "large")
            return SanitizedImageSet(
                derivatives=derivatives,
                source_width=source_width,
                source_height=source_height,
                sanitized_checksum_sha256=large.checksum_sha256,
                perceptual_hash=self._average_hash(clean),
            )
        finally:
            clean.close()

    def _validate_image(self, image: Image.Image, source: ValidatedImageSource) -> None:
        if image.format != source.source_format or image.format not in _SUPPORTED_SIGNATURES:
            raise ImageRejectedError("UNSUPPORTED_IMAGE_FORMAT")
        width, height = image.size
        if (
            width <= 0
            or height <= 0
            or width > self._max_dimension
            or height > self._max_dimension
            or width * height > self._max_pixels
        ):
            raise ImageRejectedError("IMAGE_DECOMPRESSION_BOMB")

    @staticmethod
    def _metadata_free_rgb(image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            clean = Image.new("RGB", rgba.size, "white")
            clean.paste(rgba, mask=rgba.getchannel("A"))
            rgba.close()
            return clean
        rgb = image.convert("RGB")
        clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())
        rgb.close()
        return clean

    @staticmethod
    def _encode_derivative(
        image: Image.Image, *, kind: str, max_dimension: int
    ) -> SanitizedDerivative:
        resized = image.copy()
        try:
            resized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            output = BytesIO()
            resized.save(
                output,
                format="JPEG",
                quality=85,
                optimize=True,
                progressive=True,
            )
            content = output.getvalue()
            return SanitizedDerivative(
                kind=kind,
                content=content,
                content_type="image/jpeg",
                width=resized.width,
                height=resized.height,
                checksum_sha256=sha256(content).hexdigest(),
            )
        finally:
            resized.close()

    @staticmethod
    def _average_hash(image: Image.Image) -> str:
        reduced = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
        try:
            pixels = list(reduced.tobytes())
        finally:
            reduced.close()
        average = sum(pixels) / len(pixels)
        bits = 0
        for value in pixels:
            bits = (bits << 1) | int(value >= average)
        return f"{bits:016x}"


def _decode_service_checksum(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return base64.b64decode(value, validate=True).hex()
    except (binascii.Error, ValueError) as exc:
        raise ImageRejectedError("OBJECT_CHECKSUM_MISMATCH") from exc
