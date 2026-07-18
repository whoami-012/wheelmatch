from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings
from app.core.config.settings import IdentityVerificationProviderName
from app.modules.verification.provider import (
    DeterministicIdentityVerificationProvider,
    ProviderPermanentError,
    ProviderTransientError,
)
from app.modules.verification.schemas import (
    IdentityVerificationStartResponse,
    IdentityVerificationStatusResponse,
)
from app.modules.verification.state import (
    classify_provider_failure,
    require_transition,
    result_disposition,
    transition_allowed,
)

ATTEMPT_ID = UUID("018f0000-0000-7000-8000-000000000101")
USER_ID = UUID("018f0000-0000-7000-8000-000000000102")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        pytest.param("session_pending", "pending", id="session-ready"),
        pytest.param("session_pending", "failed", id="session-failed"),
        pytest.param("pending", "manual_review", id="needs-review"),
        pytest.param("pending", "verified", id="verified"),
        pytest.param("pending", "failed", id="failed"),
    ],
)
def test_valid_identity_attempt_transitions(current: str, target: str) -> None:
    assert transition_allowed(current, target)
    require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        pytest.param("session_pending", "verified", id="session-skips-pending"),
        pytest.param("pending", "revoked", id="revocation-deferred"),
        pytest.param("verified", "failed", id="verified-terminal"),
        pytest.param("failed", "verified", id="failed-terminal"),
        pytest.param("manual_review", "verified", id="review-terminal-for-provider"),
    ],
)
def test_invalid_and_terminal_identity_attempt_transitions(current: str, target: str) -> None:
    assert not transition_allowed(current, target)
    with pytest.raises(ValueError):
        require_transition(current, target)


def test_provider_result_duplicate_conflict_and_stale_rules() -> None:
    assert (
        result_disposition(
            attempt_status="verified",
            attempt_event_id="event-1",
            result_event_id="event-1",
            result_matches=True,
            superseded=False,
            projection_current=True,
        )
        == "duplicate"
    )
    assert (
        result_disposition(
            attempt_status="pending",
            attempt_event_id=None,
            result_event_id="event-stale",
            result_matches=False,
            superseded=True,
            projection_current=False,
        )
        == "stale"
    )
    with pytest.raises(ValueError):
        result_disposition(
            attempt_status="verified",
            attempt_event_id="event-1",
            result_event_id="event-2",
            result_matches=False,
            superseded=False,
            projection_current=True,
        )


@pytest.mark.asyncio
async def test_deterministic_adapter_session_and_result_are_stable() -> None:
    provider = DeterministicIdentityVerificationProvider()
    first = await provider.create_session(
        attempt_id=ATTEMPT_ID,
        user_id=USER_ID,
        idempotency_reference=str(ATTEMPT_ID),
    )
    second = await provider.create_session(
        attempt_id=ATTEMPT_ID,
        user_id=USER_ID,
        idempotency_reference=str(ATTEMPT_ID),
    )
    result = provider.result(attempt_id=ATTEMPT_ID, event_id="event-1", status="manual_review")

    assert first.provider_reference == second.provider_reference
    assert first.capture_url == second.capture_url
    assert result.provider_reference == first.provider_reference
    assert result.event_id == "event-1"


def test_transient_and_permanent_provider_failures_are_classified_safely() -> None:
    transient = classify_provider_failure(ProviderTransientError("provider detail"))
    permanent = classify_provider_failure(ProviderPermanentError("provider detail"))

    assert transient.retryable is True
    assert transient.safe_failure_code == "PROVIDER_TEMPORARY"
    assert permanent.retryable is False
    assert permanent.safe_failure_code == "PROVIDER_UNAVAILABLE"


def test_capture_url_is_start_only_and_status_schema_rejects_it() -> None:
    now = datetime.now(UTC)
    start = IdentityVerificationStartResponse.model_validate(
        {
            "attempt_id": ATTEMPT_ID,
            "status": "pending",
            "capture_url": "https://verify.local.test/capture/value",
            "capture_expires_at": now + timedelta(minutes=15),
        }
    )
    assert start.capture_url is not None
    with pytest.raises(ValidationError):
        IdentityVerificationStatusResponse.model_validate(
            {
                "attempt_id": ATTEMPT_ID,
                "status": "pending",
                "assurance_level": None,
                "verified_at": None,
                "expires_at": None,
                "revoked_at": None,
                "version": 1,
                "failure_code": None,
                "updated_at": now,
                "capture_url": start.capture_url,
            }
        )


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(IdentityVerificationProviderName.DISABLED, id="disabled"),
        pytest.param(IdentityVerificationProviderName.DETERMINISTIC, id="deterministic"),
    ],
)
def test_non_local_configuration_rejects_development_verification_providers(
    provider: IdentityVerificationProviderName,
) -> None:
    with pytest.raises(ValidationError, match="production identity verification provider"):
        Settings(
            _env_file=None,
            environment=Environment.STAGING,
            identity_verification_provider=provider,
        )
