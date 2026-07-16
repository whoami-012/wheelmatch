from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.errors.handlers import install_exception_handlers
from app.core.health.router import router as health_router
from app.core.health.service import DatabaseProbe, HealthService, RedisProbe
from app.core.telemetry import configure_logging, configure_sentry
from app.core.telemetry.middleware import RequestContextMiddleware

logger = structlog.get_logger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    health_service: HealthService | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(level=resolved_settings.log_level)
    configure_sentry(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        if health_service is not None:
            app.state.health_service = health_service
            logger.info("application_started", **resolved_settings.safe_summary())
            yield
            logger.info("application_stopped")
            return

        database = Database.create(resolved_settings)
        redis = Redis.from_url(
            resolved_settings.redis_url.get_secret_value(),
            encoding="utf-8",
            decode_responses=True,
        )
        app.state.database = database
        app.state.redis = redis
        app.state.health_service = HealthService(
            probes={
                "database": DatabaseProbe(database.engine),
                "redis": RedisProbe(redis),
            },
            timeout_seconds=resolved_settings.readiness_timeout_seconds,
        )
        logger.info("application_started", **resolved_settings.safe_summary())
        try:
            yield
        finally:
            await redis.aclose()
            await database.close()
            logger.info("application_stopped")

    app = FastAPI(
        title="WheelMatch API",
        version=resolved_settings.service_version,
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.environment.value != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved_settings
    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)
    app.include_router(health_router)
    return app
