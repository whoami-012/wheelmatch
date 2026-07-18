from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class GateDecision:
    name: str
    state: str
    code: str | None
    remediation_action: str


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    now: datetime
    account_authorized: bool
    seller_ready: bool
    details_complete: bool
    canonical_associated: bool
    location_present: bool
    identity_status: str | None
    identity_expires_at: datetime | None
    identity_revoked_at: datetime | None
    ownership_status: str | None
    ownership_expires_at: datetime | None
    ownership_revoked_at: datetime | None
    ownership_matches_current: bool
    ownership_fingerprint_matches: bool
    active_media_statuses: tuple[str, ...]
    listing_evidence_stale: bool = False
    media_evidence_stale: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessEvaluation:
    gates: tuple[GateDecision, ...]
    moderation_status: str
    publishable: bool = False

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(gate.code for gate in self.gates if gate.code is not None))

    @property
    def pre_moderation_ready(self) -> bool:
        return all(gate.state == "ready" for gate in self.gates[:-1])

    @property
    def submission_status(self) -> str:
        verification_names = {"identity_verification", "ownership_verification"}
        if any(
            gate.name in verification_names and gate.state != "ready" for gate in self.gates[:-1]
        ):
            return "verification_pending"
        if not self.pre_moderation_ready:
            return "blocked"
        return "moderation_pending"


class ReadinessPolicy:
    policy_version = 1

    def evaluate(self, snapshot: ReadinessSnapshot) -> ReadinessEvaluation:
        gates = [
            self._simple(
                "account_owner",
                snapshot.account_authorized,
                "SELLER_RESTRICTED",
                "Restore an active authorized personal-owner account.",
            ),
            self._simple(
                "seller_readiness",
                snapshot.seller_ready,
                "SELLER_RESTRICTED",
                "Complete seller activation requirements.",
            ),
            self._details_gate(snapshot),
            self._simple(
                "canonical_vehicle",
                snapshot.canonical_associated,
                "OWNERSHIP_VERIFICATION_REQUIRED",
                "Complete vehicle ownership verification.",
            ),
            self._simple(
                "location",
                snapshot.location_present,
                "LISTING_LOCATION_REQUIRED",
                "Add the private listing location.",
            ),
            self._verification_gate(
                name="identity_verification",
                status=snapshot.identity_status,
                now=snapshot.now,
                expires_at=snapshot.identity_expires_at,
                revoked_at=snapshot.identity_revoked_at,
                required_code="IDENTITY_VERIFICATION_REQUIRED",
                pending_code="IDENTITY_VERIFICATION_PENDING",
                failed_code="IDENTITY_VERIFICATION_FAILED",
                expired_code="IDENTITY_VERIFICATION_EXPIRED",
                remediation="Complete current identity verification.",
            ),
            self._ownership_gate(snapshot),
            self._sanitized_media_gate(snapshot.active_media_statuses),
            self._media_queue_gate(snapshot),
        ]
        pre_moderation_ready = all(gate.state == "ready" for gate in gates)
        moderation_status = "pending" if pre_moderation_ready else "not_started"
        gates.append(
            GateDecision(
                name="moderation_approval",
                state=moderation_status,
                code="MODERATION_PENDING" if pre_moderation_ready else None,
                remediation_action=(
                    "Wait for moderation processing."
                    if pre_moderation_ready
                    else "Resolve the preceding readiness gates."
                ),
            )
        )
        return ReadinessEvaluation(gates=tuple(gates), moderation_status=moderation_status)

    @staticmethod
    def _simple(name: str, ready: bool, code: str, remediation: str) -> GateDecision:
        return GateDecision(
            name=name,
            state="ready" if ready else "blocked",
            code=None if ready else code,
            remediation_action="No action required." if ready else remediation,
        )

    def _details_gate(self, snapshot: ReadinessSnapshot) -> GateDecision:
        if snapshot.listing_evidence_stale:
            return GateDecision(
                name="listing_details",
                state="stale",
                code="LISTING_VERSION_CHANGED",
                remediation_action="Submit the current listing version again.",
            )
        return self._simple(
            "listing_details",
            snapshot.details_complete,
            "LISTING_DETAILS_INCOMPLETE",
            "Complete the listing details and typed vehicle specification.",
        )

    @staticmethod
    def _verification_gate(
        *,
        name: str,
        status: str | None,
        now: datetime,
        expires_at: datetime | None,
        revoked_at: datetime | None,
        required_code: str,
        pending_code: str,
        failed_code: str,
        expired_code: str,
        remediation: str,
    ) -> GateDecision:
        if status is None:
            state, code = "blocked", required_code
        elif status in {"session_pending", "pending", "manual_review"}:
            state, code = "pending", pending_code
        elif status == "expired" or (
            status == "verified" and (expires_at is None or expires_at <= now)
        ):
            state, code = "blocked", expired_code
        elif status == "verified" and revoked_at is None:
            state, code = "ready", None
        else:
            state, code = "blocked", failed_code
        return GateDecision(
            name=name,
            state=state,
            code=code,
            remediation_action="No action required." if code is None else remediation,
        )

    def _ownership_gate(self, snapshot: ReadinessSnapshot) -> GateDecision:
        status = snapshot.ownership_status
        if status == "verified" and snapshot.ownership_revoked_at is None:
            if (
                snapshot.ownership_expires_at is None
                or snapshot.ownership_expires_at <= snapshot.now
            ):
                state, code = "blocked", "OWNERSHIP_VERIFICATION_EXPIRED"
            elif not snapshot.ownership_matches_current:
                state, code = "blocked", "OWNERSHIP_VERIFICATION_REQUIRED"
            elif not snapshot.ownership_fingerprint_matches:
                state, code = "stale", "OWNERSHIP_FINGERPRINT_MISMATCH"
            else:
                state, code = "ready", None
        elif status is None:
            state, code = "blocked", "OWNERSHIP_VERIFICATION_REQUIRED"
        elif status in {"session_pending", "pending", "manual_review"}:
            state, code = "pending", "OWNERSHIP_VERIFICATION_PENDING"
        elif status == "expired":
            state, code = "blocked", "OWNERSHIP_VERIFICATION_EXPIRED"
        elif status == "revoked" or snapshot.ownership_revoked_at is not None:
            state, code = "blocked", "OWNERSHIP_VERIFICATION_REVOKED"
        else:
            state, code = "blocked", "OWNERSHIP_VERIFICATION_FAILED"
        return GateDecision(
            name="ownership_verification",
            state=state,
            code=code,
            remediation_action=(
                "No action required."
                if code is None
                else "Complete a current vehicle ownership verification."
            ),
        )

    @staticmethod
    def _sanitized_media_gate(statuses: tuple[str, ...]) -> GateDecision:
        ready = any(status == "moderation_pending" for status in statuses)
        return ReadinessPolicy._simple(
            "sanitized_media",
            ready,
            "MEDIA_PROCESSING_INCOMPLETE",
            "Add and finish processing at least one listing image.",
        )

    @staticmethod
    def _media_queue_gate(snapshot: ReadinessSnapshot) -> GateDecision:
        if snapshot.media_evidence_stale:
            return GateDecision(
                name="media_moderation_queue",
                state="stale",
                code="LISTING_VERSION_CHANGED",
                remediation_action="Submit the current media set again.",
            )
        ready = bool(snapshot.active_media_statuses) and all(
            status == "moderation_pending" for status in snapshot.active_media_statuses
        )
        return GateDecision(
            name="media_moderation_queue",
            state="ready" if ready else "pending",
            code=None if ready else "MEDIA_PROCESSING_INCOMPLETE",
            remediation_action=(
                "No action required."
                if ready
                else "Wait for all selected listing images to finish processing."
            ),
        )
