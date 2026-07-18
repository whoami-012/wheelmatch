from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

OwnershipBasis = Literal[
    "registered_owner",
    "company_vehicle",
    "financed_or_leased",
    "inherited",
    "authorized_representative",
]
OwnershipStatus = Literal[
    "session_pending", "pending", "manual_review", "verified", "failed", "expired", "revoked"
]


class OwnershipVerificationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_listing_version: int = Field(ge=1)
    jurisdiction: str = Field(min_length=2, max_length=16)
    registration: str = Field(min_length=4, max_length=32)
    vin: str | None = Field(default=None, min_length=1, max_length=32)
    chassis: str | None = Field(default=None, min_length=1, max_length=48)
    ownership_basis: OwnershipBasis


class OwnershipVerificationStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    canonical_vehicle_id: UUID
    listing_version: int
    status: OwnershipStatus
    ownership_basis: OwnershipBasis
    reused: bool
    capture_url: str | None
    capture_expires_at: datetime | None


class OwnershipVerificationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    canonical_vehicle_id: UUID
    status: OwnershipStatus
    ownership_basis: OwnershipBasis
    verified_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    failure_code: str | None
    reused: bool
    updated_at: datetime


class OwnershipProviderResultApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    status: OwnershipStatus
    disposition: Literal["applied", "duplicate", "stale"]
