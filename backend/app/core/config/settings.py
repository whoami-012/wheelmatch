from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="WHEELMATCH_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "wheelmatch-backend"
    service_version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"

    database_url: SecretStr = SecretStr("postgresql+asyncpg://postgres@localhost:5432/wheelmatch")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = "http://localhost:4566"
    sqs_events_queue_url: SecretStr = SecretStr(
        "http://localhost:4566/000000000000/wheelmatch-events"
    )
    s3_media_bucket: str = "wheelmatch-media-local"
    secret_bundle_name: str | None = None

    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    readiness_timeout_seconds: float = Field(default=2.0, gt=0.0, le=10.0)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    worker_wait_time_seconds: int = Field(default=10, ge=1, le=20)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @model_validator(mode="after")
    def reject_local_endpoints_in_production(self) -> Settings:
        if self.environment is Environment.PRODUCTION:
            database_url = self.database_url.get_secret_value().lower()
            redis_url = self.redis_url.get_secret_value().lower()
            if "localhost" in database_url or "localhost" in redis_url:
                raise ValueError("production data stores cannot use localhost")
            if self.aws_endpoint_url is not None:
                raise ValueError("production AWS endpoint override must be unset")
        return self

    def safe_summary(self) -> dict[str, str | float | int | None]:
        return {
            "service_name": self.service_name,
            "service_version": self.service_version,
            "environment": self.environment.value,
            "log_level": self.log_level,
            "aws_region": self.aws_region,
            "aws_endpoint_url": "configured" if self.aws_endpoint_url else None,
            "s3_media_bucket": self.s3_media_bucket,
            "sentry": "configured" if self.sentry_dsn else "disabled",
            "readiness_timeout_seconds": self.readiness_timeout_seconds,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
