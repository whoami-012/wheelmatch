from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID


class ProviderTransientError(Exception):
    """A retryable provider failure with a stable, non-provider-specific code."""


class ProviderPermanentError(Exception):
    """A non-retryable provider failure with a stable, non-provider-specific code."""


@dataclass(frozen=True, slots=True)
class ProviderSession:
    provider_reference: str
    capture_url: str
    capture_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderResult:
    provider_reference: str
    event_id: str
    status: str
    assurance_level: str | None = None
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    safe_failure_code: str | None = None


class IdentityVerificationProvider(Protocol):
    identifier: str

    async def create_session(
        self, *, attempt_id: UUID, user_id: UUID, idempotency_reference: str
    ) -> ProviderSession: ...


class DisabledIdentityVerificationProvider:
    identifier = "disabled"

    async def create_session(
        self, *, attempt_id: UUID, user_id: UUID, idempotency_reference: str
    ) -> ProviderSession:
        del attempt_id, user_id, idempotency_reference
        raise ProviderPermanentError("PROVIDER_UNAVAILABLE")


class DeterministicIdentityVerificationProvider:
    """Local/test-only provider with stable session and result identifiers."""

    identifier = "deterministic"

    async def create_session(
        self, *, attempt_id: UUID, user_id: UUID, idempotency_reference: str
    ) -> ProviderSession:
        del user_id
        if idempotency_reference != str(attempt_id):
            raise ProviderPermanentError("PROVIDER_REQUEST_REJECTED")
        return ProviderSession(
            provider_reference=self.reference_for(attempt_id),
            capture_url=f"https://verify.local.test/capture/{attempt_id}",
            capture_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    def result(
        self,
        *,
        attempt_id: UUID,
        event_id: str,
        status: str,
        assurance_level: str | None = None,
        verified_at: datetime | None = None,
        expires_at: datetime | None = None,
        safe_failure_code: str | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            provider_reference=self.reference_for(attempt_id),
            event_id=event_id,
            status=status,
            assurance_level=assurance_level,
            verified_at=verified_at,
            expires_at=expires_at,
            safe_failure_code=safe_failure_code,
        )

    @staticmethod
    def reference_for(attempt_id: UUID) -> str:
        return f"deterministic:{attempt_id}"
