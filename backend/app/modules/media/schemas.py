from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MediaUploadIntentRequest(BaseModel):
    listing_id: UUID
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0, le=15_000_000)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    sort_order: int = Field(ge=0, le=19)


class MediaUploadIntentResponse(BaseModel):
    media_id: UUID
    upload_url: str
    method: Literal["PUT"] = "PUT"
    required_headers: dict[str, str]
    expires_at: datetime


class MediaCompleteRequest(BaseModel):
    size_bytes: int = Field(gt=0, le=15_000_000)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class MediaStatusResponse(BaseModel):
    media_id: UUID
    listing_id: UUID
    status: Literal[
        "intent_created",
        "processing",
        "scanning",
        "moderation_pending",
        "rejected",
        "removed",
        "expired",
        "failed",
    ]
    content_type: str
    size_bytes: int
    sort_order: int
    expires_at: datetime
    processing_version: int
    failure_code: str | None = None
