from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings
from app.core.config.settings import (
    OwnershipVerificationProviderName,
    VehicleIdentityNormalizerName,
)
from app.modules.verification.ownership_models import VehicleOwnershipVerification
from app.modules.verification.ownership_provider import (
    DeterministicOwnershipVerificationProvider,
    DisabledOwnershipVerificationProvider,
)
from app.modules.verification.ownership_schemas import OwnershipVerificationStatusResponse
from app.modules.verification.ownership_state import (
    ownership_result_disposition,
    ownership_transition_allowed,
    require_ownership_transition,
)
from app.modules.verification.provider import ProviderPermanentError, ProviderTransientError
from app.modules.verification.state import classify_provider_failure
from app.modules.verification.vehicle_identity import (
    DeterministicVehicleIdentityNormalizer,
    NormalizedVehicleIdentity,
    VehicleIdentityInvalid,
    key_vehicle_identity,
    ownership_material_fingerprint,
)

ATTEMPT_ID = UUID("018f0000-0000-7000-8000-000000000201")
OWNER_ID = UUID("018f0000-0000-7000-8000-000000000202")
CANONICAL_ID = UUID("018f0000-0000-7000-8000-000000000203")
IDENTITY_ATTEMPT_ID = UUID("018f0000-0000-7000-8000-000000000204")
KEY = b"unit-test-vehicle-identity-key"


def normalized_identity() -> NormalizedVehicleIdentity:
    return DeterministicVehicleIdentityNormalizer().normalize(
        jurisdiction="in-kl",
        registration="KL-07 AB 1234",
        vin="MA3EUA61S00123456",
        chassis="CHASSIS12345",
    )


def test_normalization_and_keyed_hmac_are_deterministic_and_versioned() -> None:
    normalized = normalized_identity()
    first = key_vehicle_identity(normalized, key=KEY, hash_version=1)
    same = key_vehicle_identity(normalized, key=KEY, hash_version=1)
    next_version = key_vehicle_identity(normalized, key=KEY, hash_version=2)

    assert first == same
    assert first != next_version
    assert first.jurisdiction == "IN-KL"
    assert first.registration_hmac != "KL07AB1234"
    assert len(first.registration_hmac) == 64


@pytest.mark.parametrize(
    ("jurisdiction", "registration", "vin", "chassis"),
    [
        pytest.param("india", "KL07AB1234", None, "CHASSIS123", id="jurisdiction"),
        pytest.param("IN-KL", "***", None, "CHASSIS123", id="registration"),
        pytest.param("IN-KL", "KL07AB1234", "INVALIDVIN", None, id="vin"),
        pytest.param("IN-KL", "KL07AB1234", None, None, id="missing-secondary"),
    ],
)
def test_invalid_vehicle_identifiers_are_rejected(
    jurisdiction: str, registration: str, vin: str | None, chassis: str | None
) -> None:
    with pytest.raises(VehicleIdentityInvalid):
        DeterministicVehicleIdentityNormalizer().normalize(
            jurisdiction=jurisdiction,
            registration=registration,
            vin=vin,
            chassis=chassis,
        )


def fingerprint(
    *,
    owner_user_id: UUID = OWNER_ID,
    identity_projection_version: int = 4,
    provider_result_version: int = 1,
    provider_material_attributes: dict[str, str] | None = None,
) -> str:
    return ownership_material_fingerprint(
        key=KEY,
        canonical_vehicle_id=CANONICAL_ID,
        canonical_identity_version=1,
        owner_user_id=owner_user_id,
        identity_attempt_id=IDENTITY_ATTEMPT_ID,
        identity_projection_version=identity_projection_version,
        jurisdiction="IN-KL",
        ownership_basis="registered_owner",
        registration_hmac="a" * 64,
        vin_hmac="b" * 64,
        chassis_hmac=None,
        provider_result_version=provider_result_version,
        provider_material_attributes=provider_material_attributes or {"decision": "matched"},
    )


def test_material_fingerprint_includes_material_and_excludes_listing_content() -> None:
    baseline = fingerprint()
    assert fingerprint(owner_user_id=UUID(int=9)) != baseline
    assert fingerprint(identity_projection_version=5) != baseline
    assert fingerprint(provider_result_version=2) != baseline
    assert fingerprint(provider_material_attributes={"decision": "review"}) != baseline

    non_material_listing = {"price": 100, "media": ["one"], "location": "old"}
    before = fingerprint()
    non_material_listing.update(price=200, media=["two"], location="new")
    assert fingerprint() == before


@pytest.mark.parametrize(
    ("current", "target"),
    [
        pytest.param("session_pending", "pending", id="session-ready"),
        pytest.param("session_pending", "failed", id="session-failed"),
        pytest.param("pending", "manual_review", id="review"),
        pytest.param("pending", "verified", id="verified"),
        pytest.param("pending", "failed", id="failed"),
    ],
)
def test_valid_ownership_transitions(current: str, target: str) -> None:
    assert ownership_transition_allowed(current, target)
    require_ownership_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        pytest.param("session_pending", "verified", id="skip-pending"),
        pytest.param("verified", "failed", id="terminal-verified"),
        pytest.param("failed", "verified", id="terminal-failed"),
        pytest.param("manual_review", "verified", id="terminal-review"),
    ],
)
def test_invalid_and_terminal_ownership_transitions(current: str, target: str) -> None:
    assert not ownership_transition_allowed(current, target)
    with pytest.raises(ValueError):
        require_ownership_transition(current, target)


def test_provider_result_duplicate_conflict_and_stale_rules() -> None:
    assert (
        ownership_result_disposition(
            attempt_status="verified",
            attempt_event_id="event-1",
            result_event_id="event-1",
            result_matches=True,
            stale=False,
        )
        == "duplicate"
    )
    assert (
        ownership_result_disposition(
            attempt_status="pending",
            attempt_event_id=None,
            result_event_id="event-2",
            result_matches=False,
            stale=True,
        )
        == "stale"
    )
    with pytest.raises(ValueError):
        ownership_result_disposition(
            attempt_status="verified",
            attempt_event_id="event-1",
            result_event_id="event-2",
            result_matches=False,
            stale=False,
        )


@pytest.mark.asyncio
async def test_deterministic_and_disabled_provider_behavior() -> None:
    provider = DeterministicOwnershipVerificationProvider()
    first = await provider.create_session(
        attempt_id=ATTEMPT_ID,
        owner_user_id=OWNER_ID,
        idempotency_reference=str(ATTEMPT_ID),
    )
    second = await provider.create_session(
        attempt_id=ATTEMPT_ID,
        owner_user_id=OWNER_ID,
        idempotency_reference=str(ATTEMPT_ID),
    )
    assert first.provider_reference == second.provider_reference
    assert first.capture_url == second.capture_url
    with pytest.raises(ProviderPermanentError):
        await DisabledOwnershipVerificationProvider().create_session(
            attempt_id=ATTEMPT_ID,
            owner_user_id=OWNER_ID,
            idempotency_reference=str(ATTEMPT_ID),
        )


def test_provider_failures_are_classified_without_internal_details() -> None:
    assert classify_provider_failure(ProviderTransientError("private")).safe_failure_code == (
        "PROVIDER_TEMPORARY"
    )
    assert classify_provider_failure(ProviderPermanentError("private")).safe_failure_code == (
        "PROVIDER_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    ("normalizer", "provider"),
    [
        pytest.param(
            VehicleIdentityNormalizerName.DISABLED,
            OwnershipVerificationProviderName.DISABLED,
            id="disabled",
        ),
        pytest.param(
            VehicleIdentityNormalizerName.DETERMINISTIC,
            OwnershipVerificationProviderName.DETERMINISTIC,
            id="deterministic",
        ),
    ],
)
def test_non_local_configuration_rejects_development_adapters(
    normalizer: VehicleIdentityNormalizerName, provider: OwnershipVerificationProviderName
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment=Environment.STAGING,
            vehicle_identity_normalizer=normalizer,
            ownership_verification_provider=provider,
        )


def test_status_and_persistence_schemas_exclude_capture_and_identity_material() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        OwnershipVerificationStatusResponse.model_validate(
            {
                "attempt_id": ATTEMPT_ID,
                "canonical_vehicle_id": CANONICAL_ID,
                "status": "pending",
                "ownership_basis": "registered_owner",
                "verified_at": None,
                "expires_at": None,
                "revoked_at": None,
                "failure_code": None,
                "updated_at": now,
                "capture_url": "https://ownership.local.test/private",
            }
        )
    columns = set(VehicleOwnershipVerification.__table__.columns.keys())
    assert not columns.intersection(
        {"registration", "vin", "chassis", "capture_url", "provider_payload", "document_bytes"}
    )
