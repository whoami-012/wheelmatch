from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog

_SENSITIVE_FRAGMENTS = frozenset(
    {
        "authorization",
        "cookie",
        "email",
        "password",
        "phone",
        "secret",
        "token",
        "verification_code",
    }
)


def _is_sensitive(key: str) -> bool:
    normalized = key.casefold()
    return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)


def redact_audit_changes(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return an allowlisted-shape audit payload with secret and PII values removed."""
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        if _is_sensitive(key):
            redacted[key] = "[REDACTED]"
        elif isinstance(item, Mapping):
            redacted[key] = redact_audit_changes(item)
        elif isinstance(item, (str, int, float, bool)) or item is None:
            redacted[key] = item
        else:
            redacted[key] = str(item)
    return redacted


class AuditRecorder:
    def record(
        self,
        session: AsyncSession,
        *,
        action: str,
        outcome: str,
        resource_type: str,
        actor_user_id: UUID | None = None,
        resource_id: UUID | None = None,
        organization_id: UUID | None = None,
        membership_id: UUID | None = None,
        reason_code: str | None = None,
        changes: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AuditLog:
        audit = AuditLog(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            membership_id=membership_id,
            action=action,
            outcome=outcome,
            reason_code=reason_code,
            resource_type=resource_type,
            resource_id=resource_id,
            changes=redact_audit_changes(changes or {}),
            request_id=request_id,
            trace_id=trace_id,
        )
        session.add(audit)
        return audit
