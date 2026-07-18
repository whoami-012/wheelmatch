from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import IdempotencyConflictError, IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.catalogue.models import CanonicalVehicle
from app.modules.listings.models import Listing
from app.modules.listings.readiness import ReadinessEvaluation, ReadinessPolicy, ReadinessSnapshot
from app.modules.listings.repository import ListingRepository
from app.modules.listings.service import ListingService
from app.modules.listings.submission_models import ListingSubmissionAttempt
from app.modules.listings.submission_repository import ListingSubmissionRepository
from app.modules.listings.submission_schemas import (
    ListingSubmissionRequest,
    PublicationReadinessResponse,
    ReadinessGateResponse,
)
from app.modules.locations.repository import LocationRepository
from app.modules.media.models import ListingMedia
from app.modules.media.repository import MediaRepository
from app.modules.profiles.repository import ProfileRepository
from app.modules.verification.models import UserVerificationState
from app.modules.verification.ownership_models import VehicleOwnershipVerification
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.ownership_reuse import (
    OwnershipEvidence,
    OwnershipReuseContext,
    OwnershipReusePolicy,
)
from app.modules.verification.repository import VerificationRepository


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    evaluation: ReadinessEvaluation
    identity_state: UserVerificationState | None
    ownership: VehicleOwnershipVerification | None
    ownership_reused: bool
    ownership_reuse_policy_version: int | None
    ownership_effective_expires_at: datetime | None
    media_set_fingerprint: str
    media_set_version: int


class ListingSubmissionService:
    def __init__(
        self,
        *,
        listing_service: ListingService,
        listing_repository: ListingRepository,
        submission_repository: ListingSubmissionRepository,
        profile_repository: ProfileRepository,
        location_repository: LocationRepository,
        media_repository: MediaRepository,
        verification_repository: VerificationRepository,
        ownership_repository: OwnershipVerificationRepository,
        ownership_reuse_policy: OwnershipReusePolicy | None = None,
        policy: ReadinessPolicy,
        audit: AuditRecorder,
        idempotency_repository: IdempotencyRepository,
        event_writer: Any = enqueue_event,
    ) -> None:
        self._listings = listing_service
        self._listing_repository = listing_repository
        self._submissions = submission_repository
        self._profiles = profile_repository
        self._locations = location_repository
        self._media = media_repository
        self._verification = verification_repository
        self._ownership = ownership_repository
        self._ownership_reuse = ownership_reuse_policy or OwnershipReusePolicy(
            freshness_days=180, policy_version=1
        )
        self._policy = policy
        self._audit = audit
        self._idempotency = idempotency_repository
        self._event_writer = event_writer

    async def submit(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
        request: ListingSubmissionRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> PublicationReadinessResponse:
        now = datetime.now(UTC)
        scope = f"user:{actor_user_id}:listing:{listing_id}"
        async with session.begin():
            try:
                reservation = await self._idempotency.reserve(
                    session,
                    scope=scope,
                    operation="listing.submit",
                    key=idempotency_key,
                    request_hash=request_hash,
                    expires_at=now + timedelta(hours=24),
                )
            except IdempotencyConflictError as exc:
                raise AppError(
                    status=409,
                    code="IDEMPOTENCY_KEY_CONFLICT",
                    title="Idempotency key conflicts with an earlier request",
                ) from exc

            listing, _, membership_id = await self._listings.get_authorized_listing(
                session,
                actor_user_id=actor_user_id,
                listing_id=listing_id,
                for_update=True,
            )
            self._require_personal(listing)
            if not reservation.acquired:
                if reservation.replay_body is None:
                    raise AppError(
                        status=409,
                        code="IDEMPOTENCY_IN_PROGRESS",
                        title="An idempotent request is already in progress",
                    )
                return PublicationReadinessResponse.model_validate(reservation.replay_body)
            if listing.version != request.expected_version:
                raise AppError(
                    status=409,
                    code="LISTING_VERSION_CHANGED",
                    title="Listing state changed",
                )

            existing = await self._submissions.get_for_version(
                session,
                listing_id=listing.id,
                listing_version=listing.version,
                for_update=True,
            )
            evidence = await self._evaluate(
                session,
                listing=listing,
                existing_attempt=None,
                now=now,
                for_update=True,
            )
            await self._submissions.supersede_other_versions(
                session,
                listing_id=listing.id,
                current_version=listing.version,
                superseded_at=now,
            )
            attempt, request_moderation = await self._record_attempt(
                session,
                listing=listing,
                actor_user_id=actor_user_id,
                existing=existing,
                evidence=evidence,
                now=now,
            )
            listing.publication_status = "pending"
            listing.moderation_status = evidence.evaluation.moderation_status
            listing.submitted_listing_version = listing.version
            listing.submitted_at = now

            self._audit.record(
                session,
                action="listing.submission.recorded",
                outcome="success",
                resource_type="listing_submission",
                actor_user_id=actor_user_id,
                membership_id=membership_id,
                resource_id=attempt.id,
                changes={
                    "listing_id": str(listing.id),
                    "listing_version": listing.version,
                    "attempt_number": attempt.attempt_number,
                    "submission_status": attempt.submission_status,
                    "safe_codes": ",".join(attempt.blocker_codes),
                    "policy_version": attempt.policy_version,
                    "ownership_reused": attempt.ownership_reused,
                    "ownership_reuse_policy_version": (attempt.ownership_reuse_policy_version),
                },
                request_id=get_request_id(),
            )
            if request_moderation:
                self._event_writer(
                    session,
                    event_type="listing.moderation.requested",
                    aggregate_type="listing",
                    aggregate_id=listing.id,
                    payload={
                        "listing_id": str(listing.id),
                        "listing_version": listing.version,
                        "submission_attempt_id": str(attempt.id),
                        "attempt_number": attempt.attempt_number,
                        "owner_user_id": str(attempt.owner_user_id),
                        "identity_projection_version": attempt.identity_projection_version,
                        "ownership_verification_id": (
                            str(attempt.ownership_verification_id)
                            if attempt.ownership_verification_id
                            else None
                        ),
                        "ownership_result_version": attempt.ownership_result_version,
                        "ownership_reused": attempt.ownership_reused,
                        "ownership_reuse_policy_version": (attempt.ownership_reuse_policy_version),
                        "media_set_version": attempt.media_set_version,
                        "policy_version": attempt.policy_version,
                        "status": attempt.submission_status,
                        "safe_codes": attempt.blocker_codes,
                    },
                )
            await session.flush()
            response = self._response(
                listing=listing,
                attempt=attempt,
                evidence=evidence,
                evaluated_at=now,
            )
            await self._idempotency.complete(
                session,
                scope=scope,
                operation="listing.submit",
                key=idempotency_key,
                response_status=200,
                response_body=response.model_dump(mode="json"),
                resource_type="listing_submission",
                resource_id=attempt.id,
            )
        return response

    async def readiness(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
    ) -> PublicationReadinessResponse:
        listing, _, _ = await self._listings.get_authorized_listing(
            session, actor_user_id=actor_user_id, listing_id=listing_id
        )
        self._require_personal(listing)
        latest = await self._submissions.get_latest(session, listing_id=listing.id)
        evidence = await self._evaluate(
            session,
            listing=listing,
            existing_attempt=latest,
            now=datetime.now(UTC),
            for_update=False,
        )
        return self._response(
            listing=listing,
            attempt=latest,
            evidence=evidence,
            evaluated_at=datetime.now(UTC),
        )

    async def _evaluate(
        self,
        session: AsyncSession,
        *,
        listing: Listing,
        existing_attempt: ListingSubmissionAttempt | None,
        now: datetime,
        for_update: bool,
    ) -> SourceEvidence:
        seller = await self._profiles.get_seller_profile(
            session, cast(UUID, listing.owner_user_id), for_update=for_update
        )
        vehicle_spec, car_spec, bike_spec = await self._listing_repository.get_specs(
            session, listing.id
        )
        location = await self._locations.get(session, listing.id)
        identity = await self._verification.get_state(
            session, cast(UUID, listing.owner_user_id), for_update=for_update
        )
        canonical: CanonicalVehicle | None = None
        ownership: VehicleOwnershipVerification | None = None
        ownership_reused = False
        ownership_reuse_policy_version: int | None = None
        ownership_effective_expires_at: datetime | None = None
        ownership_status: str | None = None
        if listing.canonical_vehicle_id is not None and identity is not None:
            canonical = await self._ownership.get_canonical(
                session, listing.canonical_vehicle_id, for_update=for_update
            )
            attempts = await self._ownership.list_for_reuse(
                session,
                owner_user_id=cast(UUID, listing.owner_user_id),
                canonical_vehicle_id=listing.canonical_vehicle_id,
                for_update=for_update,
            )
            if canonical is not None:
                current = next(
                    (
                        row
                        for row in attempts
                        if row.listing_id == listing.id and row.status == "verified"
                    ),
                    None,
                )
                verified = current or next(
                    (row for row in attempts if row.status == "verified"), None
                )
                basis = verified.ownership_basis if verified else "registered_owner"
                identity_valid = bool(
                    identity.effective_status == "verified"
                    and identity.expires_at is not None
                    and identity.expires_at > now
                    and identity.revoked_at is None
                )
                selection = self._ownership_reuse.select(
                    context=OwnershipReuseContext(
                        now=now,
                        owner_user_id=cast(UUID, listing.owner_user_id),
                        canonical_vehicle_id=canonical.id,
                        identity_verification_id=identity.current_attempt_id,
                        identity_projection_version=identity.version,
                        vehicle_identity_version=canonical.identity_version,
                        vehicle_hash_version=canonical.hash_version,
                        vehicle_identity_status=canonical.identity_status,
                        ownership_basis=basis,
                        identity_verified=identity_valid,
                        personal_listing=True,
                    ),
                    evidence=tuple(self._reuse_evidence(row) for row in attempts),
                    current_listing_id=listing.id,
                )
                if selection.evidence is not None and selection.decision.eligible:
                    ownership = next(
                        row for row in attempts if row.id == selection.evidence.attempt_id
                    )
                    ownership_reused = selection.decision.reused
                    ownership_reuse_policy_version = selection.decision.policy_version
                    ownership_effective_expires_at = selection.decision.effective_expires_at
                    ownership_status = ownership.status
                else:
                    latest_current = next(
                        (row for row in attempts if row.listing_id == listing.id), None
                    )
                    ownership = latest_current
                    ownership_status = self._blocked_ownership_status(
                        selection.decision.code,
                        fallback=latest_current.status if latest_current else None,
                    )
        media = await self._media.list_for_readiness(session, listing.id, for_update=for_update)
        media_fingerprint, media_version = self._media_set(media)
        details_complete = (
            bool(listing.title and listing.title.strip())
            and bool(listing.description and listing.description.strip())
            and listing.asking_price is not None
            and listing.asking_price > 0
            and listing.variant_id is not None
            and vehicle_spec is not None
            and (
                (listing.vehicle_type == "car" and car_spec is not None and bike_spec is None)
                or (listing.vehicle_type == "bike" and bike_spec is not None and car_spec is None)
            )
        )
        ownership_matches = bool(
            ownership
            and canonical
            and identity
            and ownership_status == "verified"
            and ownership.owner_user_id == listing.owner_user_id
            and ownership.canonical_vehicle_id == listing.canonical_vehicle_id
            and ownership.identity_verification_id == identity.current_attempt_id
            and ownership.identity_projection_version == identity.version
            and ownership.vehicle_identity_version == canonical.identity_version
            and ownership.provider_result_version is not None
        )
        ownership_fingerprint_matches = bool(
            ownership
            and ownership.material_fingerprint
            and (
                existing_attempt is None
                or existing_attempt.ownership_material_fingerprint == ownership.material_fingerprint
            )
        )
        snapshot = ReadinessSnapshot(
            now=now,
            account_authorized=True,
            seller_ready=bool(
                seller and seller.status == "active" and seller.readiness_state == "ready"
            ),
            details_complete=details_complete,
            canonical_associated=canonical is not None,
            location_present=location is not None,
            identity_status=identity.effective_status if identity else None,
            identity_expires_at=identity.expires_at if identity else None,
            identity_revoked_at=identity.revoked_at if identity else None,
            ownership_status=ownership_status,
            ownership_expires_at=(
                ownership_effective_expires_at
                if ownership_reused
                else ownership.expires_at
                if ownership
                else None
            ),
            ownership_revoked_at=ownership.revoked_at if ownership else None,
            ownership_matches_current=ownership_matches,
            ownership_fingerprint_matches=ownership_fingerprint_matches,
            active_media_statuses=tuple(item.status for item in media),
            listing_evidence_stale=bool(
                existing_attempt and existing_attempt.listing_version != listing.version
            ),
            media_evidence_stale=bool(
                existing_attempt and existing_attempt.media_set_fingerprint != media_fingerprint
            ),
        )
        return SourceEvidence(
            evaluation=self._policy.evaluate(snapshot),
            identity_state=identity,
            ownership=ownership,
            ownership_reused=ownership_reused,
            ownership_reuse_policy_version=ownership_reuse_policy_version,
            ownership_effective_expires_at=ownership_effective_expires_at,
            media_set_fingerprint=media_fingerprint,
            media_set_version=media_version,
        )

    async def _record_attempt(
        self,
        session: AsyncSession,
        *,
        listing: Listing,
        actor_user_id: UUID,
        existing: ListingSubmissionAttempt | None,
        evidence: SourceEvidence,
        now: datetime,
    ) -> tuple[ListingSubmissionAttempt, bool]:
        previous_status = existing.submission_status if existing else None
        attempt = existing
        if attempt is None:
            attempt = ListingSubmissionAttempt(
                id=uuid7(),
                listing_id=listing.id,
                listing_version=listing.version,
                attempt_number=await self._submissions.next_attempt_number(
                    session, listing_id=listing.id
                ),
                actor_user_id=actor_user_id,
                owner_user_id=cast(UUID, listing.owner_user_id),
                submission_status=evidence.evaluation.submission_status,
                media_set_fingerprint=evidence.media_set_fingerprint,
                media_set_version=evidence.media_set_version,
                policy_version=self._policy.policy_version,
                blocker_codes=list(evidence.evaluation.blocker_codes),
                submitted_at=now,
            )
            session.add(attempt)
        attempt.actor_user_id = actor_user_id
        attempt.submission_status = evidence.evaluation.submission_status
        attempt.identity_verification_id = (
            evidence.identity_state.current_attempt_id if evidence.identity_state else None
        )
        attempt.identity_projection_version = (
            evidence.identity_state.version if evidence.identity_state else None
        )
        attempt.ownership_verification_id = evidence.ownership.id if evidence.ownership else None
        attempt.ownership_result_version = (
            evidence.ownership.provider_result_version if evidence.ownership else None
        )
        attempt.ownership_material_fingerprint = (
            evidence.ownership.material_fingerprint if evidence.ownership else None
        )
        attempt.ownership_reused = evidence.ownership_reused
        attempt.ownership_reuse_policy_version = evidence.ownership_reuse_policy_version
        attempt.ownership_effective_expires_at = (
            evidence.ownership_effective_expires_at if evidence.ownership_reused else None
        )
        attempt.media_set_fingerprint = evidence.media_set_fingerprint
        attempt.media_set_version = evidence.media_set_version
        attempt.policy_version = self._policy.policy_version
        attempt.blocker_codes = list(evidence.evaluation.blocker_codes)
        attempt.submitted_at = now
        request_moderation = (
            attempt.submission_status == "moderation_pending"
            and previous_status != "moderation_pending"
        )
        return attempt, request_moderation

    @staticmethod
    def _reuse_evidence(attempt: VehicleOwnershipVerification) -> OwnershipEvidence:
        return OwnershipEvidence(
            attempt_id=attempt.id,
            listing_id=attempt.listing_id,
            attempt_number=attempt.attempt_number,
            owner_user_id=attempt.owner_user_id,
            canonical_vehicle_id=attempt.canonical_vehicle_id,
            identity_verification_id=attempt.identity_verification_id,
            identity_projection_version=attempt.identity_projection_version,
            vehicle_identity_version=attempt.vehicle_identity_version,
            hash_version=attempt.hash_version,
            ownership_basis=attempt.ownership_basis,
            material_fingerprint=attempt.material_fingerprint,
            provider_result_version=attempt.provider_result_version,
            status=attempt.status,
            verified_at=attempt.verified_at,
            expires_at=attempt.expires_at,
            revoked_at=attempt.revoked_at,
            superseded_at=attempt.superseded_at,
        )

    @staticmethod
    def _blocked_ownership_status(code: str | None, *, fallback: str | None) -> str | None:
        if code == "OWNERSHIP_VERIFICATION_EXPIRED":
            return "expired"
        if code == "OWNERSHIP_VERIFICATION_REVOKED":
            return "revoked"
        if code == "OWNERSHIP_VERIFICATION_PENDING":
            return "pending"
        return fallback

    @staticmethod
    def _media_set(media: list[ListingMedia]) -> tuple[str, int]:
        material = [
            {
                "id": str(item.id),
                "processing_version": item.processing_version,
                "status": item.status,
            }
            for item in media
        ]
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        version = max(1, len(media) + sum(item.processing_version for item in media))
        return hashlib.sha256(encoded).hexdigest(), version

    @staticmethod
    def _response(
        *,
        listing: Listing,
        attempt: ListingSubmissionAttempt | None,
        evidence: SourceEvidence,
        evaluated_at: datetime,
    ) -> PublicationReadinessResponse:
        return PublicationReadinessResponse(
            listing_id=listing.id,
            listing_version=listing.version,
            submission_attempt_id=attempt.id if attempt else None,
            submission_status=attempt.submission_status if attempt else "not_submitted",
            publication_status=listing.publication_status,
            moderation_status=evidence.evaluation.moderation_status,
            ownership_reused=evidence.ownership_reused,
            publishable=False,
            gates=[
                ReadinessGateResponse(
                    name=gate.name,
                    state=gate.state,
                    code=gate.code,
                    remediation_action=gate.remediation_action,
                )
                for gate in evidence.evaluation.gates
            ],
            evaluated_at=evaluated_at,
        )

    @staticmethod
    def _require_personal(listing: Listing) -> None:
        if listing.owner_type == "dealer_organization":
            raise AppError(
                status=409,
                code="DEALER_SUBMISSION_NOT_IMPLEMENTED",
                title="Dealer listing submission is not implemented",
            )
        if (
            listing.owner_type != "user"
            or listing.owner_user_id is None
            or listing.owner_organization_id is not None
        ):
            raise ListingService.listing_not_found()
