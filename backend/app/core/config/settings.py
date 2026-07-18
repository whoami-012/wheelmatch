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


class MediaScannerProvider(StrEnum):
    DISABLED = "disabled"
    DETERMINISTIC = "deterministic"


class IdentityVerificationProviderName(StrEnum):
    DISABLED = "disabled"
    DETERMINISTIC = "deterministic"


class VehicleIdentityNormalizerName(StrEnum):
    DISABLED = "disabled"
    DETERMINISTIC = "deterministic"


class OwnershipVerificationProviderName(StrEnum):
    DISABLED = "disabled"
    DETERMINISTIC = "deterministic"


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

    secret_hash_key: SecretStr = SecretStr("local-development-secret-hash-key-change-me")
    access_token_signing_key: SecretStr = SecretStr(
        "local-development-access-token-signing-key-change-me"
    )
    access_token_issuer: str = "wheelmatch-backend"
    access_token_audience: str = "wheelmatch-mobile"
    access_token_ttl_seconds: int = Field(default=900, ge=300, le=3600)
    refresh_session_ttl_seconds: int = Field(default=2592000, ge=86400, le=7776000)
    login_failure_threshold: int = Field(default=5, ge=3, le=20)
    login_lock_seconds: int = Field(default=900, ge=60, le=86400)
    verification_challenge_ttl_seconds: int = Field(default=900, ge=300, le=3600)
    recovery_challenge_ttl_seconds: int = Field(default=1800, ge=300, le=3600)
    dealer_invitation_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    authorization_cache_ttl_seconds: int = Field(default=60, ge=5, le=300)
    registration_rate_limit: int = Field(default=5, ge=1, le=100)
    login_rate_limit: int = Field(default=10, ge=1, le=1000)
    verification_rate_limit: int = Field(default=10, ge=1, le=1000)
    recovery_rate_limit: int = Field(default=5, ge=1, le=100)
    refresh_rate_limit: int = Field(default=30, ge=1, le=1000)

    readiness_timeout_seconds: float = Field(default=2.0, gt=0.0, le=10.0)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    worker_wait_time_seconds: int = Field(default=10, ge=1, le=20)
    media_scanner_provider: MediaScannerProvider = MediaScannerProvider.DISABLED
    media_processing_max_input_bytes: int = Field(default=15_000_000, ge=1_000_000, le=25_000_000)
    media_processing_max_pixels: int = Field(default=25_000_000, ge=1_000_000, le=50_000_000)
    media_processing_max_dimension: int = Field(default=12_000, ge=1_000, le=20_000)
    media_processing_max_output_dimension: int = Field(default=1_600, ge=640, le=4_096)
    media_processing_max_attempts: int = Field(default=5, ge=1, le=10)
    media_processing_lease_seconds: int = Field(default=120, ge=30, le=900)
    identity_verification_provider: IdentityVerificationProviderName = (
        IdentityVerificationProviderName.DISABLED
    )
    vehicle_identity_normalizer: VehicleIdentityNormalizerName = (
        VehicleIdentityNormalizerName.DISABLED
    )
    vehicle_identity_hmac_key: SecretStr = SecretStr(
        "local-development-vehicle-identity-hmac-key-change-me"
    )
    vehicle_identity_hash_version: int = Field(default=1, ge=1)
    ownership_verification_provider: OwnershipVerificationProviderName = (
        OwnershipVerificationProviderName.DISABLED
    )
    ownership_reuse_freshness_days: int = Field(default=180, ge=1, le=365)
    ownership_reuse_policy_version: int = Field(default=1, ge=1, le=1000)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @model_validator(mode="after")
    def reject_local_endpoints_in_production(self) -> Settings:
        if self.environment not in {Environment.LOCAL, Environment.TEST}:
            if self.identity_verification_provider in {
                IdentityVerificationProviderName.DISABLED,
                IdentityVerificationProviderName.DETERMINISTIC,
            }:
                raise ValueError(
                    "a production identity verification provider must be configured "
                    "before deployment"
                )
            if self.vehicle_identity_normalizer in {
                VehicleIdentityNormalizerName.DISABLED,
                VehicleIdentityNormalizerName.DETERMINISTIC,
            }:
                raise ValueError(
                    "an approved vehicle identity normalizer must be configured before deployment"
                )
            if self.ownership_verification_provider in {
                OwnershipVerificationProviderName.DISABLED,
                OwnershipVerificationProviderName.DETERMINISTIC,
            }:
                raise ValueError(
                    "a production ownership verification provider must be configured "
                    "before deployment"
                )
            raise ValueError(
                "a production-grade media malware scanner must be configured before deployment"
            )
        if self.environment is Environment.PRODUCTION:
            database_url = self.database_url.get_secret_value().lower()
            redis_url = self.redis_url.get_secret_value().lower()
            if "localhost" in database_url or "localhost" in redis_url:
                raise ValueError("production data stores cannot use localhost")
            if self.aws_endpoint_url is not None:
                raise ValueError("production AWS endpoint override must be unset")
            if self.secret_hash_key.get_secret_value().startswith("local-development-"):
                raise ValueError("production secret hash key must be configured")
            if self.access_token_signing_key.get_secret_value().startswith("local-development-"):
                raise ValueError("production access token signing key must be configured")
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
            "media_scanner_provider": self.media_scanner_provider.value,
            "identity_verification_provider": self.identity_verification_provider.value,
            "vehicle_identity_normalizer": self.vehicle_identity_normalizer.value,
            "vehicle_identity_hash_version": self.vehicle_identity_hash_version,
            "ownership_verification_provider": self.ownership_verification_provider.value,
            "ownership_reuse_freshness_days": self.ownership_reuse_freshness_days,
            "ownership_reuse_policy_version": self.ownership_reuse_policy_version,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
