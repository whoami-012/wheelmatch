from __future__ import annotations

import re
import time

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.ids import uuid7
from app.core.telemetry.context import set_request_id

logger = structlog.get_logger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_TRACEPARENT_PATTERN = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inbound_request_id = request.headers.get("x-request-id", "")
        request_id = (
            inbound_request_id
            if _REQUEST_ID_PATTERN.fullmatch(inbound_request_id)
            else str(uuid7())
        )
        traceparent = request.headers.get("traceparent")
        if traceparent and not _TRACEPARENT_PATTERN.fullmatch(traceparent):
            traceparent = None

        set_request_id(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            traceparent=traceparent,
        )
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-request-id"] = request_id
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["referrer-policy"] = "no-referrer"
            return response
        finally:
            logger.info(
                "http_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()
