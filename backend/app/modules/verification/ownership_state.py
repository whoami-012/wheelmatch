from __future__ import annotations

OWNERSHIP_BASES = frozenset(
    {
        "registered_owner",
        "company_vehicle",
        "financed_or_leased",
        "inherited",
        "authorized_representative",
    }
)
OWNERSHIP_ACTIVE_STATUSES = frozenset({"session_pending", "pending", "manual_review"})
OWNERSHIP_TERMINAL_STATUSES = frozenset(
    {"manual_review", "verified", "failed", "expired", "revoked"}
)
OWNERSHIP_RESULT_STATUSES = frozenset({"manual_review", "verified", "failed"})

_TRANSITIONS = {
    "session_pending": frozenset({"pending", "failed"}),
    "pending": frozenset({"manual_review", "verified", "failed"}),
}


def ownership_transition_allowed(current: str, target: str) -> bool:
    return current == target or target in _TRANSITIONS.get(current, frozenset())


def require_ownership_transition(current: str, target: str) -> None:
    if current in OWNERSHIP_TERMINAL_STATUSES or not ownership_transition_allowed(current, target):
        raise ValueError("invalid ownership verification transition")


def ownership_result_disposition(
    *,
    attempt_status: str,
    attempt_event_id: str | None,
    result_event_id: str,
    result_matches: bool,
    stale: bool,
) -> str:
    if stale:
        return "stale"
    if attempt_event_id == result_event_id and result_matches:
        return "duplicate"
    if attempt_status in OWNERSHIP_TERMINAL_STATUSES or attempt_event_id == result_event_id:
        raise ValueError("provider result conflicts with finalized ownership state")
    return "applied"
