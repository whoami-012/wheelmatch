from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.modules.verification.provider import ProviderPermanentError


@dataclass(frozen=True, slots=True)
class OwnershipProviderSession:
    provider_reference: str
    capture_url: str
    capture_expires_at: datetime


@dataclass(frozen=True, slots=True)
class OwnershipProviderResult:
    provider_reference: str
    event_id: str
    status: str
    result_version: int = 1
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    safe_failure_code: str | None = None
    material_attributes: Mapping[str, str] = field(default_factory=dict)


class OwnershipVerificationProvider(Protocol):
    identifier: str

    async def create_session(
        self, *, attempt_id: UUID, owner_user_id: UUID, idempotency_reference: str
    ) -> OwnershipProviderSession: ...


class DisabledOwnershipVerificationProvider:
    identifier = "disabled"

    async def create_session(
        self, *, attempt_id: UUID, owner_user_id: UUID, idempotency_reference: str
    ) -> OwnershipProviderSession:
        del attempt_id, owner_user_id, idempotency_reference
        raise ProviderPermanentError("PROVIDER_UNAVAILABLE")


class DeterministicOwnershipVerificationProvider:
    identifier = "deterministic"

    async def create_session(
        self, *, attempt_id: UUID, owner_user_id: UUID, idempotency_reference: str
    ) -> OwnershipProviderSession:
        del owner_user_id
        if idempotency_reference != str(attempt_id):
            raise ProviderPermanentError("PROVIDER_REQUEST_REJECTED")
        return OwnershipProviderSession(
            provider_reference=self.reference_for(attempt_id),
            capture_url=f"https://ownership.local.test/capture/{attempt_id}",
            capture_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    def result(
        self,
        *,
        attempt_id: UUID,
        event_id: str,
        status: str,
        result_version: int = 1,
        verified_at: datetime | None = None,
        expires_at: datetime | None = None,
        safe_failure_code: str | None = None,
        material_attributes: Mapping[str, str] | None = None,
    ) -> OwnershipProviderResult:
        return OwnershipProviderResult(
            provider_reference=self.reference_for(attempt_id),
            event_id=event_id,
            status=status,
            result_version=result_version,
            verified_at=verified_at,
            expires_at=expires_at,
            safe_failure_code=safe_failure_code,
            material_attributes=material_attributes or {},
        )

    @staticmethod
    def reference_for(attempt_id: UUID) -> str:
        return f"deterministic:{attempt_id}"
