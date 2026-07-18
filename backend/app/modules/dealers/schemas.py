from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DealerRole = Literal["owner", "admin", "inventory_manager", "sales_agent"]
MembershipStatusUpdate = Literal["active", "suspended", "revoked"]


class OrganizationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=2, max_length=200)
    display_name: str = Field(min_length=2, max_length=120)

    @field_validator("legal_name", "display_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("name is too short")
        return normalized


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    legal_name: str
    display_name: str
    status: str
    verification_status: str
    authorization_version: int
    created_at: datetime


class MembershipInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    role: DealerRole


class MembershipAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitation_token: SecretStr = Field(min_length=32, max_length=256)


class MembershipUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: DealerRole | None = None
    status: MembershipStatusUpdate | None = None
    expected_version: int = Field(ge=1)


class MembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    organization_id: UUID
    user_id: UUID
    role: str
    status: str
    version: int
    invite_expires_at: datetime | None
    accepted_at: datetime | None
    suspended_at: datetime | None
    left_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class MembershipListItem(MembershipResponse):
    organization_display_name: str
    organization_status: str
    organization_verification_status: str
