from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, Settings


def test_safe_summary_never_contains_secret_urls() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://user:secret@database/wheelmatch"),
        redis_url=SecretStr("redis://:secret@redis/0"),
        sqs_events_queue_url=SecretStr("https://private.example/queue"),
        sentry_dsn=SecretStr("https://secret@example.invalid/1"),
    )

    summary = str(settings.safe_summary())

    assert "secret" not in summary
    assert "private.example" not in summary
    assert "configured" in summary


def test_production_rejects_local_service_endpoints() -> None:
    with pytest.raises(ValidationError):
        Settings(environment=Environment.PRODUCTION)


def test_production_requires_aws_endpoint_override_to_be_unset() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment=Environment.PRODUCTION,
            database_url=SecretStr("postgresql+asyncpg://user:secret@database/wheelmatch"),
            redis_url=SecretStr("rediss://redis.internal/0"),
            aws_endpoint_url="https://local-emulator.invalid",
        )
