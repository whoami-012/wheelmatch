from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OwnershipReuseContext:
    now: datetime
    owner_user_id: UUID
    canonical_vehicle_id: UUID
    identity_verification_id: UUID
    identity_projection_version: int
    vehicle_identity_version: int
    vehicle_hash_version: int
    vehicle_identity_status: str
    ownership_basis: str
    identity_verified: bool = True
    personal_listing: bool = True


@dataclass(frozen=True, slots=True)
class OwnershipEvidence:
    attempt_id: UUID
    listing_id: UUID
    attempt_number: int
    owner_user_id: UUID
    canonical_vehicle_id: UUID
    identity_verification_id: UUID
    identity_projection_version: int
    vehicle_identity_version: int
    hash_version: int
    ownership_basis: str
    material_fingerprint: str
    provider_result_version: int | None
    status: str
    verified_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    superseded_at: datetime | None


@dataclass(frozen=True, slots=True)
class OwnershipReuseDecision:
    eligible: bool
    reused: bool
    code: str | None
    effective_expires_at: datetime | None
    policy_version: int | None


@dataclass(frozen=True, slots=True)
class OwnershipSelection:
    evidence: OwnershipEvidence | None
    decision: OwnershipReuseDecision


class OwnershipReusePolicy:
    def __init__(self, *, freshness_days: int, policy_version: int) -> None:
        if not 1 <= freshness_days <= 365:
            raise ValueError("ownership reuse freshness must be between 1 and 365 days")
        if not 1 <= policy_version <= 1000:
            raise ValueError("ownership reuse policy version must be between 1 and 1000")
        self.freshness_days = freshness_days
        self.policy_version = policy_version

    def select(
        self,
        *,
        context: OwnershipReuseContext,
        evidence: tuple[OwnershipEvidence, ...],
        current_listing_id: UUID,
    ) -> OwnershipSelection:
        ordered = tuple(sorted(evidence, key=lambda item: item.attempt_number, reverse=True))
        current = next(
            (
                item
                for item in ordered
                if item.listing_id == current_listing_id and item.status == "verified"
            ),
            None,
        )
        if current is not None:
            decision = self.evaluate(
                context=context,
                evidence=current,
                newer_conflicting=self._newer_conflicting(current, ordered),
                reuse=False,
            )
            if decision.eligible:
                return OwnershipSelection(evidence=current, decision=decision)

        reusable = next(
            (
                item
                for item in ordered
                if item.listing_id != current_listing_id and item.status == "verified"
            ),
            None,
        )
        if reusable is None:
            return OwnershipSelection(
                evidence=None,
                decision=OwnershipReuseDecision(
                    eligible=False,
                    reused=False,
                    code=self._latest_safe_code(ordered),
                    effective_expires_at=None,
                    policy_version=None,
                ),
            )
        decision = self.evaluate(
            context=context,
            evidence=reusable,
            newer_conflicting=self._newer_conflicting(reusable, ordered),
            reuse=True,
        )
        return OwnershipSelection(
            evidence=reusable if decision.eligible else None,
            decision=decision,
        )

    def evaluate(
        self,
        *,
        context: OwnershipReuseContext,
        evidence: OwnershipEvidence,
        newer_conflicting: bool,
        reuse: bool = True,
    ) -> OwnershipReuseDecision:
        effective_expiry = self.effective_expiry(evidence, reuse=reuse)
        code: str | None = None
        if not context.personal_listing:
            code = "DEALER_OWNERSHIP_VERIFICATION_UNSUPPORTED"
        elif evidence.status in {"session_pending", "pending", "manual_review"}:
            code = "OWNERSHIP_VERIFICATION_PENDING"
        elif evidence.status == "revoked" or evidence.revoked_at is not None:
            code = "OWNERSHIP_VERIFICATION_REVOKED"
        elif evidence.status != "verified":
            code = "OWNERSHIP_REUSE_NOT_AVAILABLE"
        elif evidence.superseded_at is not None or newer_conflicting:
            code = "OWNERSHIP_REUSE_CONFLICT"
        elif effective_expiry is None or effective_expiry <= context.now:
            code = "OWNERSHIP_VERIFICATION_EXPIRED"
        elif (
            not context.identity_verified
            or evidence.owner_user_id != context.owner_user_id
            or evidence.canonical_vehicle_id != context.canonical_vehicle_id
            or evidence.identity_verification_id != context.identity_verification_id
            or evidence.identity_projection_version != context.identity_projection_version
            or evidence.vehicle_identity_version != context.vehicle_identity_version
            or evidence.hash_version != context.vehicle_hash_version
            or evidence.ownership_basis != context.ownership_basis
            or context.vehicle_identity_status != "active"
            or evidence.provider_result_version is None
            or evidence.provider_result_version <= 0
            or not self._fingerprint_valid(evidence.material_fingerprint)
        ):
            code = "OWNERSHIP_REUSE_NOT_AVAILABLE"
        return OwnershipReuseDecision(
            eligible=code is None,
            reused=reuse and code is None,
            code=code,
            effective_expires_at=effective_expiry if code is None else None,
            policy_version=self.policy_version if reuse and code is None else None,
        )

    def effective_expiry(
        self, evidence: OwnershipEvidence, *, reuse: bool = True
    ) -> datetime | None:
        if evidence.verified_at is None or evidence.expires_at is None:
            return None
        if not reuse:
            return evidence.expires_at
        return min(
            evidence.expires_at,
            evidence.verified_at + timedelta(days=self.freshness_days),
        )

    @staticmethod
    def _newer_conflicting(
        candidate: OwnershipEvidence, evidence: tuple[OwnershipEvidence, ...]
    ) -> bool:
        return any(
            item.attempt_number > candidate.attempt_number and item.superseded_at is None
            for item in evidence
        )

    @staticmethod
    def _latest_safe_code(evidence: tuple[OwnershipEvidence, ...]) -> str:
        if not evidence:
            return "OWNERSHIP_VERIFICATION_REQUIRED"
        latest = evidence[0]
        if latest.status in {"session_pending", "pending", "manual_review"}:
            return "OWNERSHIP_VERIFICATION_PENDING"
        if latest.status == "revoked" or latest.revoked_at is not None:
            return "OWNERSHIP_VERIFICATION_REVOKED"
        if latest.status == "expired":
            return "OWNERSHIP_VERIFICATION_EXPIRED"
        return "OWNERSHIP_REUSE_NOT_AVAILABLE"

    @staticmethod
    def _fingerprint_valid(value: str) -> bool:
        return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
