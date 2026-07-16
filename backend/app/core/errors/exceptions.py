from __future__ import annotations

from typing import Any

from app.core.errors.models import FieldError


class AppError(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        field_errors: list[FieldError] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.field_errors = field_errors or []
        self.meta = meta or {}
