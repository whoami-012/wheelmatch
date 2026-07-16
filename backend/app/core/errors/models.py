from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    message: str
    code: str


class ProblemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int = Field(ge=400, le=599)
    code: str
    detail: str | None = None
    correlation_id: str
    field_errors: list[FieldError] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
