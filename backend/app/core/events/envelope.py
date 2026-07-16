from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.ids import uuid7


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid7)
    event_type: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    schema_version: int = Field(default=1, ge=1, le=1000)
    aggregate_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")
    aggregate_id: UUID
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    traceparent: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)
