from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

VerificationStatus = Literal[
    "session_pending", "pending", "manual_review", "verified", "failed", "expired", "revoked"
]
AssuranceLevel = Literal["basic", "standard", "enhanced"]


class IdentityVerificationStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    status: VerificationStatus
    capture_url: str | None
    capture_expires_at: datetime | None


class IdentityVerificationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    status: VerificationStatus
    assurance_level: AssuranceLevel | None
    verified_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    version: int
    failure_code: str | None
    updated_at: datetime


class ProviderResultApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    status: VerificationStatus
    projection_version: int
    disposition: Literal["applied", "duplicate", "stale"]
