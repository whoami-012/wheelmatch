from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=8, max_length=16)
    password: SecretStr
    display_name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("display name cannot be blank")
        return normalized


class VerificationDispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    challenge_id: UUID


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    status: str
    verification_challenges: list[VerificationDispatchResponse]


class VerifyChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: SecretStr = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class VerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool
    kind: str


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class RecoveryAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool = True


class RecoveryResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=32, max_length=256)
    new_password: SecretStr


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr
    new_password: SecretStr


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr
    device_name: str | None = Field(default=None, min_length=1, max_length=120)
    device_platform: Literal["android", "ios", "web", "desktop", "unknown"] = "unknown"


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: SecretStr = Field(min_length=32, max_length=256)


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_expires_at: datetime
    session_id: UUID


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    device_name: str | None
    device_platform: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    current: bool
