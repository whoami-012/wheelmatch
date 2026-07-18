from __future__ import annotations

from dataclasses import dataclass

from app.modules.verification.provider import ProviderPermanentError, ProviderTransientError

ACTIVE_STATUSES = frozenset({"session_pending", "pending", "manual_review"})
TERMINAL_STATUSES = frozenset({"manual_review", "verified", "failed", "expired", "revoked"})
PROVIDER_RESULT_STATUSES = frozenset({"manual_review", "verified", "failed"})
ASSURANCE_LEVELS = frozenset({"basic", "standard", "enhanced"})

_TRANSITIONS = {
    "session_pending": frozenset({"pending", "failed"}),
    "pending": frozenset({"manual_review", "verified", "failed"}),
}


def transition_allowed(current: str, target: str) -> bool:
    return current == target or target in _TRANSITIONS.get(current, frozenset())


def require_transition(current: str, target: str) -> None:
    if current in TERMINAL_STATUSES or not transition_allowed(current, target):
        raise ValueError(f"invalid verification transition: {current} -> {target}")


def result_disposition(
    *,
    attempt_status: str,
    attempt_event_id: str | None,
    result_event_id: str,
    result_matches: bool,
    superseded: bool,
    projection_current: bool,
) -> str:
    if superseded or not projection_current:
        return "stale"
    if attempt_event_id == result_event_id and result_matches:
        return "duplicate"
    if attempt_status in TERMINAL_STATUSES or attempt_event_id == result_event_id:
        raise ValueError("provider result conflicts with finalized verification state")
    return "applied"


@dataclass(frozen=True, slots=True)
class ProviderFailureClassification:
    retryable: bool
    safe_failure_code: str


def classify_provider_failure(error: Exception) -> ProviderFailureClassification:
    if isinstance(error, ProviderTransientError):
        return ProviderFailureClassification(retryable=True, safe_failure_code="PROVIDER_TEMPORARY")
    if isinstance(error, ProviderPermanentError):
        return ProviderFailureClassification(
            retryable=False, safe_failure_code="PROVIDER_UNAVAILABLE"
        )
    return ProviderFailureClassification(retryable=True, safe_failure_code="PROVIDER_TEMPORARY")
