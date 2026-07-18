from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    email: str
    phone: str | None
    email_verified: bool
    phone_verified: bool
    display_name: str | None
    home_locality: str | None
    version: int
    updated_at: datetime


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=100)
    home_locality: str | None = Field(default=None, max_length=120)
    expected_version: int = Field(ge=1)

    @field_validator("display_name", "home_locality")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class SellerProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SellerProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    status: str
    readiness_state: str
    activated_at: datetime | None
    missing_requirements: list[str]


class DealerCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID
    membership_id: UUID
    role: str
    permissions: list[str]


class CapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer: bool
    personal_seller: bool
    can_create_private_draft: bool
    dealer: list[DealerCapabilityResponse]
