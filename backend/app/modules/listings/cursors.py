from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.errors import AppError


@dataclass(frozen=True, slots=True)
class ListingCursor:
    updated_at: datetime
    listing_id: UUID


class ListingCursorCodec:
    def __init__(self, signing_key: str, *, ttl_seconds: int = 3600) -> None:
        self._key = signing_key.encode("utf-8")
        self._ttl = ttl_seconds

    def encode(self, cursor: ListingCursor, *, filter_key: str) -> str:
        payload = {
            "updated_at": cursor.updated_at.isoformat(),
            "listing_id": str(cursor.listing_id),
            "filter": hashlib.sha256(filter_key.encode()).hexdigest(),
            "expires_at": int((datetime.now(UTC) + timedelta(seconds=self._ttl)).timestamp()),
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.new(self._key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode()

    def decode(self, value: str, *, filter_key: str) -> ListingCursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = base64.urlsafe_b64decode(padded)
            raw, signature = decoded[:-32], decoded[-32:]
            expected = hmac.new(self._key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            payload = json.loads(raw)
            expected_filter = hashlib.sha256(filter_key.encode()).hexdigest()
            if payload["filter"] != expected_filter:
                raise ValueError("filter")
            if int(payload["expires_at"]) < int(datetime.now(UTC).timestamp()):
                raise ValueError("expired")
            return ListingCursor(
                updated_at=datetime.fromisoformat(payload["updated_at"]),
                listing_id=UUID(payload["listing_id"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(
                status=422,
                code="LISTING_CURSOR_INVALID",
                title="Listing cursor is invalid or expired",
            ) from exc
