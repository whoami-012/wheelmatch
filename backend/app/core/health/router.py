from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.core.errors import AppError

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    version: str


class ReadinessResponse(LivenessResponse):
    checks: dict[str, str]


@router.get("/live", response_model=LivenessResponse)
async def liveness(request: Request) -> LivenessResponse:
    settings: Any = request.app.state.settings
    return LivenessResponse(
        status="ok",
        service=settings.service_name,
        version=settings.service_version,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse:
    settings: Any = request.app.state.settings
    checks = await request.app.state.health_service.readiness()
    if any(status != "ok" for status in checks.values()):
        raise AppError(
            status=503,
            code="SERVICE_NOT_READY",
            title="Service is not ready",
            detail="One or more required dependencies are unavailable.",
            meta={"checks": checks},
        )
    return ReadinessResponse(
        status="ok",
        service=settings.service_name,
        version=settings.service_version,
        checks=checks,
    )
