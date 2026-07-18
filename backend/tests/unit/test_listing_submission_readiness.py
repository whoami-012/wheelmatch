from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.listings.readiness import ReadinessPolicy, ReadinessSnapshot
from app.modules.listings.submission_schemas import PublicationReadinessResponse

NOW = datetime(2026, 7, 18, tzinfo=UTC)
LISTING_ID = UUID("018f0000-0000-7000-8000-000000000401")


def ready_snapshot() -> ReadinessSnapshot:
    return ReadinessSnapshot(
        now=NOW,
        account_authorized=True,
        seller_ready=True,
        details_complete=True,
        canonical_associated=True,
        location_present=True,
        identity_status="verified",
        identity_expires_at=NOW + timedelta(days=30),
        identity_revoked_at=None,
        ownership_status="verified",
        ownership_expires_at=NOW + timedelta(days=30),
        ownership_revoked_at=None,
        ownership_matches_current=True,
        ownership_fingerprint_matches=True,
        active_media_statuses=("moderation_pending",),
    )


@pytest.mark.parametrize(
    ("changes", "gate_name", "code"),
    [
        pytest.param(
            {"account_authorized": False}, "account_owner", "SELLER_RESTRICTED", id="account"
        ),
        pytest.param({"seller_ready": False}, "seller_readiness", "SELLER_RESTRICTED", id="seller"),
        pytest.param(
            {"details_complete": False},
            "listing_details",
            "LISTING_DETAILS_INCOMPLETE",
            id="details",
        ),
        pytest.param(
            {"canonical_associated": False},
            "canonical_vehicle",
            "OWNERSHIP_VERIFICATION_REQUIRED",
            id="canonical",
        ),
        pytest.param(
            {"location_present": False}, "location", "LISTING_LOCATION_REQUIRED", id="location"
        ),
        pytest.param(
            {"identity_status": None},
            "identity_verification",
            "IDENTITY_VERIFICATION_REQUIRED",
            id="identity-required",
        ),
        pytest.param(
            {"identity_status": "pending"},
            "identity_verification",
            "IDENTITY_VERIFICATION_PENDING",
            id="identity-pending",
        ),
        pytest.param(
            {"identity_status": "failed"},
            "identity_verification",
            "IDENTITY_VERIFICATION_FAILED",
            id="identity-failed",
        ),
        pytest.param(
            {"identity_expires_at": NOW},
            "identity_verification",
            "IDENTITY_VERIFICATION_EXPIRED",
            id="identity-expired",
        ),
        pytest.param(
            {"ownership_status": None},
            "ownership_verification",
            "OWNERSHIP_VERIFICATION_REQUIRED",
            id="ownership-required",
        ),
        pytest.param(
            {"ownership_status": "pending"},
            "ownership_verification",
            "OWNERSHIP_VERIFICATION_PENDING",
            id="ownership-pending",
        ),
        pytest.param(
            {"ownership_status": "failed"},
            "ownership_verification",
            "OWNERSHIP_VERIFICATION_FAILED",
            id="ownership-failed",
        ),
        pytest.param(
            {"ownership_expires_at": NOW},
            "ownership_verification",
            "OWNERSHIP_VERIFICATION_EXPIRED",
            id="ownership-expired",
        ),
        pytest.param(
            {"ownership_status": "revoked"},
            "ownership_verification",
            "OWNERSHIP_VERIFICATION_REVOKED",
            id="ownership-revoked",
        ),
        pytest.param(
            {"ownership_fingerprint_matches": False},
            "ownership_verification",
            "OWNERSHIP_FINGERPRINT_MISMATCH",
            id="fingerprint",
        ),
        pytest.param(
            {"active_media_statuses": ()},
            "sanitized_media",
            "MEDIA_PROCESSING_INCOMPLETE",
            id="media-required",
        ),
        pytest.param(
            {"active_media_statuses": ("moderation_pending", "processing")},
            "media_moderation_queue",
            "MEDIA_PROCESSING_INCOMPLETE",
            id="media-pending",
        ),
    ],
)
def test_readiness_policy_maps_each_gate_to_safe_codes(
    changes: dict[str, Any], gate_name: str, code: str
) -> None:
    evaluation = ReadinessPolicy().evaluate(replace(ready_snapshot(), **changes))
    gate = next(item for item in evaluation.gates if item.name == gate_name)
    assert gate.code == code
    assert code in evaluation.blocker_codes
    assert not evaluation.publishable


def test_all_pre_moderation_gates_still_cannot_publish() -> None:
    evaluation = ReadinessPolicy().evaluate(ready_snapshot())
    assert evaluation.pre_moderation_ready
    assert evaluation.submission_status == "moderation_pending"
    assert evaluation.moderation_status == "pending"
    assert evaluation.gates[-1].code == "MODERATION_PENDING"
    assert not evaluation.publishable


@pytest.mark.parametrize(
    ("changes", "gate_name"),
    [
        pytest.param({"listing_evidence_stale": True}, "listing_details", id="listing"),
        pytest.param({"media_evidence_stale": True}, "media_moderation_queue", id="media"),
    ],
)
def test_stale_listing_or_media_evidence_invalidates_readiness(
    changes: dict[str, Any], gate_name: str
) -> None:
    evaluation = ReadinessPolicy().evaluate(replace(ready_snapshot(), **changes))
    gate = next(item for item in evaluation.gates if item.name == gate_name)
    assert gate.state == "stale"
    assert gate.code == "LISTING_VERSION_CHANGED"
    assert not evaluation.pre_moderation_ready


def test_readiness_response_is_allowlisted_and_forbids_sensitive_evidence() -> None:
    evaluation = ReadinessPolicy().evaluate(ready_snapshot())
    body = {
        "listing_id": LISTING_ID,
        "listing_version": 1,
        "submission_attempt_id": None,
        "submission_status": "not_submitted",
        "publication_status": "private",
        "moderation_status": evaluation.moderation_status,
        "publishable": False,
        "gates": [
            {
                "name": gate.name,
                "state": gate.state,
                "code": gate.code,
                "remediation_action": gate.remediation_action,
            }
            for gate in evaluation.gates
        ],
        "evaluated_at": NOW,
    }
    response = PublicationReadinessResponse.model_validate(body)
    assert set(response.model_dump()) == {
        "listing_id",
        "listing_version",
        "submission_attempt_id",
        "submission_status",
        "publication_status",
        "moderation_status",
        "publishable",
        "gates",
        "evaluated_at",
    }
    with pytest.raises(ValidationError):
        PublicationReadinessResponse.model_validate(
            {**body, "material_fingerprint": "private", "exact_coordinates": [1, 2]}
        )
