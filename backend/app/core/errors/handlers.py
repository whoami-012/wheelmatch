from __future__ import annotations

from typing import Any

import orjson
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors.exceptions import AppError
from app.core.errors.models import FieldError, ProblemDetail
from app.core.telemetry.context import get_request_id

logger = structlog.get_logger(__name__)


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)


async def app_error_handler(_request: Request, exc: AppError) -> Response:
    return _problem_response(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        field_errors=exc.field_errors,
        meta=exc.meta,
    )


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> Response:
    field_errors = [
        FieldError(
            field=".".join(str(part) for part in error["loc"] if part != "body"),
            message=str(error["msg"]),
            code=str(error["type"]),
        )
        for error in exc.errors()
    ]
    return _problem_response(
        status=422,
        code="VALIDATION_ERROR",
        title="Request validation failed",
        detail="One or more request fields are invalid.",
        field_errors=field_errors,
    )


async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> Response:
    return _problem_response(
        status=exc.status_code,
        code="HTTP_ERROR",
        title="Request failed",
        detail=str(exc.detail) if exc.detail else None,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    logger.exception(
        "unhandled_request_error",
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
    )
    return _problem_response(
        status=500,
        code="INTERNAL_ERROR",
        title="Internal server error",
        detail="The request could not be completed.",
    )


def _problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    field_errors: list[FieldError] | None = None,
    meta: dict[str, Any] | None = None,
) -> Response:
    problem = ProblemDetail(
        type=f"urn:wheelmatch:error:{code.lower().replace('_', '-')}",
        title=title,
        status=status,
        code=code,
        detail=detail,
        correlation_id=get_request_id(),
        field_errors=field_errors or [],
        meta=meta or {},
    )
    return Response(
        status_code=status,
        content=orjson.dumps(problem.model_dump(mode="json")),
        media_type="application/problem+json",
    )
