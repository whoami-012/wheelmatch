from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.modules.verification.ownership_reuse import (
    OwnershipEvidence,
    OwnershipReuseContext,
    OwnershipReusePolicy,
)

NOW = datetime(2026, 7, 19, tzinfo=UTC)
OWNER_ID = UUID("018f0000-0000-7000-8000-000000000501")
CANONICAL_ID = UUID("018f0000-0000-7000-8000-000000000502")
IDENTITY_ID = UUID("018f0000-0000-7000-8000-000000000503")
SOURCE_LISTING_ID = UUID("018f0000-0000-7000-8000-000000000504")
TARGET_LISTING_ID = UUID("018f0000-0000-7000-8000-000000000505")
ATTEMPT_ID = UUID("018f0000-0000-7000-8000-000000000506")


def context() -> OwnershipReuseContext:
    return OwnershipReuseContext(
        now=NOW,
        owner_user_id=OWNER_ID,
        canonical_vehicle_id=CANONICAL_ID,
        identity_verification_id=IDENTITY_ID,
        identity_projection_version=3,
        vehicle_identity_version=2,
        vehicle_hash_version=1,
        vehicle_identity_status="active",
        ownership_basis="registered_owner",
    )


def evidence() -> OwnershipEvidence:
    return OwnershipEvidence(
        attempt_id=ATTEMPT_ID,
        listing_id=SOURCE_LISTING_ID,
        attempt_number=1,
        owner_user_id=OWNER_ID,
        canonical_vehicle_id=CANONICAL_ID,
        identity_verification_id=IDENTITY_ID,
        identity_projection_version=3,
        vehicle_identity_version=2,
        hash_version=1,
        ownership_basis="registered_owner",
        material_fingerprint="a" * 64,
        provider_result_version=1,
        status="verified",
        verified_at=NOW - timedelta(days=30),
        expires_at=NOW + timedelta(days=365),
        revoked_at=None,
        superseded_at=None,
    )


def policy() -> OwnershipReusePolicy:
    return OwnershipReusePolicy(freshness_days=180, policy_version=2)


def test_eligible_evidence_is_reusable_without_mutating_expiry() -> None:
    original = evidence()
    decision = policy().evaluate(context=context(), evidence=original, newer_conflicting=False)
    assert decision.eligible and decision.reused
    assert decision.policy_version == 2
    assert original.expires_at == NOW + timedelta(days=365)


@pytest.mark.parametrize(
    ("provider_days", "expected_days"),
    [
        pytest.param(90, 90, id="provider"),
        pytest.param(365, 180, id="policy"),
    ],
)
def test_effective_expiry_uses_earlier_provider_or_policy_limit(
    provider_days: int, expected_days: int
) -> None:
    verified_at = NOW - timedelta(days=10)
    candidate = replace(
        evidence(),
        verified_at=verified_at,
        expires_at=verified_at + timedelta(days=provider_days),
    )
    assert policy().effective_expiry(candidate) == verified_at + timedelta(days=expected_days)


@pytest.mark.parametrize(
    ("context_changes", "evidence_changes"),
    [
        pytest.param({}, {"owner_user_id": UUID(int=91)}, id="owner"),
        pytest.param({}, {"canonical_vehicle_id": UUID(int=92)}, id="canonical"),
        pytest.param({}, {"identity_verification_id": UUID(int=93)}, id="identity-attempt"),
        pytest.param({}, {"identity_projection_version": 4}, id="identity-version"),
        pytest.param({}, {"vehicle_identity_version": 3}, id="vehicle-version"),
        pytest.param({}, {"hash_version": 2}, id="hash-version"),
        pytest.param({}, {"ownership_basis": "inherited"}, id="basis"),
        pytest.param({}, {"material_fingerprint": "invalid"}, id="fingerprint"),
        pytest.param({"identity_verified": False}, {}, id="identity-current"),
    ],
)
def test_bound_identity_material_mismatches_fail_closed(
    context_changes: dict[str, Any], evidence_changes: dict[str, Any]
) -> None:
    decision = policy().evaluate(
        context=replace(context(), **context_changes),
        evidence=replace(evidence(), **evidence_changes),
        newer_conflicting=False,
    )
    assert not decision.eligible
    assert decision.code == "OWNERSHIP_REUSE_NOT_AVAILABLE"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        pytest.param(
            {"status": "revoked", "revoked_at": NOW}, "OWNERSHIP_VERIFICATION_REVOKED", id="revoked"
        ),
        pytest.param({"expires_at": NOW}, "OWNERSHIP_VERIFICATION_EXPIRED", id="provider-expired"),
        pytest.param(
            {"verified_at": NOW - timedelta(days=181)},
            "OWNERSHIP_VERIFICATION_EXPIRED",
            id="policy-expired",
        ),
        pytest.param({"superseded_at": NOW}, "OWNERSHIP_REUSE_CONFLICT", id="superseded"),
        pytest.param({"status": "manual_review"}, "OWNERSHIP_VERIFICATION_PENDING", id="review"),
        pytest.param({"status": "pending"}, "OWNERSHIP_VERIFICATION_PENDING", id="pending"),
    ],
)
def test_invalid_lifecycle_states_map_to_safe_codes(changes: dict[str, Any], code: str) -> None:
    decision = policy().evaluate(
        context=context(), evidence=replace(evidence(), **changes), newer_conflicting=False
    )
    assert not decision.eligible
    assert decision.code == code


@pytest.mark.parametrize(
    "identity_status",
    ["disputed", "transferred", "stolen", "written_off", "fraud_review"],
)
def test_restricted_vehicle_identity_states_fail_closed(identity_status: str) -> None:
    decision = policy().evaluate(
        context=replace(context(), vehicle_identity_status=identity_status),
        evidence=evidence(),
        newer_conflicting=False,
    )
    assert not decision.eligible
    assert decision.code == "OWNERSHIP_REUSE_NOT_AVAILABLE"


def test_newer_conflicting_attempt_fails_closed() -> None:
    decision = policy().evaluate(context=context(), evidence=evidence(), newer_conflicting=True)
    assert not decision.eligible
    assert decision.code == "OWNERSHIP_REUSE_CONFLICT"


def test_dealer_listing_is_unsupported() -> None:
    decision = policy().evaluate(
        context=replace(context(), personal_listing=False),
        evidence=evidence(),
        newer_conflicting=False,
    )
    assert not decision.eligible
    assert decision.code == "DEALER_OWNERSHIP_VERIFICATION_UNSUPPORTED"


def test_selection_marks_cross_listing_evidence_reused() -> None:
    selection = policy().select(
        context=context(),
        evidence=(evidence(),),
        current_listing_id=TARGET_LISTING_ID,
    )
    assert selection.evidence is not None
    assert selection.evidence.attempt_id == ATTEMPT_ID
    assert selection.decision.reused
    assert selection.decision.code is None


def test_safe_codes_contain_no_provider_or_fraud_details() -> None:
    codes = {
        policy()
        .evaluate(
            context=context(),
            evidence=replace(
                evidence(), status=status, revoked_at=NOW if status == "revoked" else None
            ),
            newer_conflicting=False,
        )
        .code
        for status in ("pending", "manual_review", "failed", "expired", "revoked")
    }
    serialized = " ".join(code or "" for code in codes).casefold()
    assert "provider" not in serialized
    assert "fraud" not in serialized
