from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListingSubmissionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ReadinessGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "account_owner",
        "seller_readiness",
        "listing_details",
        "canonical_vehicle",
        "location",
        "identity_verification",
        "ownership_verification",
        "sanitized_media",
        "media_moderation_queue",
        "moderation_approval",
    ]
    state: Literal["ready", "blocked", "pending", "stale", "not_started"]
    code: str | None
    remediation_action: str


class PublicationReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: UUID
    listing_version: int
    submission_attempt_id: UUID | None
    submission_status: Literal[
        "not_submitted", "blocked", "verification_pending", "moderation_pending"
    ]
    publication_status: Literal["private", "pending"]
    moderation_status: Literal["not_started", "pending"]
    ownership_reused: bool
    publishable: Literal[False]
    gates: list[ReadinessGateResponse]
    evaluated_at: datetime


ListingSubmissionResponse = PublicationReadinessResponse
