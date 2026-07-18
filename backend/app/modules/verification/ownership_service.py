from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import (
    IdempotencyConflictError,
    IdempotencyRepository,
    canonical_request_hash,
)
from app.core.ids import uuid7
from app.core.outbox.models import OutboxEvent
from app.core.outbox.service import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.authorization.policy import (
    AuthorizationContext,
    AuthorizationPolicy,
    DealerPermission,
    OrganizationAccess,
)
from app.modules.catalogue.models import CanonicalVehicle
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.models import Listing
from app.modules.listings.repository import ListingRepository
from app.modules.verification.models import UserVerificationState
from app.modules.verification.ownership_models import VehicleOwnershipVerification
from app.modules.verification.ownership_provider import (
    OwnershipProviderResult,
    OwnershipVerificationProvider,
)
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.ownership_reuse import (
    OwnershipEvidence,
    OwnershipReuseContext,
    OwnershipReusePolicy,
    OwnershipSelection,
)
from app.modules.verification.ownership_schemas import (
    OwnershipProviderResultApplyResponse,
    OwnershipVerificationStartRequest,
    OwnershipVerificationStartResponse,
    OwnershipVerificationStatusResponse,
)
from app.modules.verification.ownership_state import (
    OWNERSHIP_RESULT_STATUSES,
    OWNERSHIP_TERMINAL_STATUSES,
    ownership_result_disposition,
    require_ownership_transition,
)
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.state import classify_provider_failure
from app.modules.verification.vehicle_identity import (
    KeyedVehicleIdentity,
    VehicleIdentityInvalid,
    VehicleIdentityNormalizer,
    VehicleIdentityNormalizerUnavailable,
    key_vehicle_identity,
    ownership_material_fingerprint,
)

EventWriter = Callable[..., OutboxEvent]


@dataclass(frozen=True, slots=True)
class OwnershipClaim:
    attempt_id: UUID
    reused: bool
    provider_required: bool


_SAFE_FAILURE_CODES = frozenset(
    {
        "MANUAL_REVIEW_REQUIRED",
        "OWNERSHIP_MISMATCH",
        "PROVIDER_REJECTED",
        "PROVIDER_UNAVAILABLE",
        "QUALITY_INSUFFICIENT",
        "VERIFICATION_FAILED",
    }
)


class OwnershipVerificationService:
    def __init__(
        self,
        *,
        repository: OwnershipVerificationRepository,
        listing_repository: ListingRepository,
        identity_repository: IdentityRepository,
        dealer_repository: DealerRepository,
        authorization_policy: AuthorizationPolicy,
        identity_verification_repository: VerificationRepository,
        provider: OwnershipVerificationProvider,
        normalizer: VehicleIdentityNormalizer,
        hmac_key: bytes,
        hash_version: int,
        reuse_policy: OwnershipReusePolicy | None = None,
        audit: AuditRecorder,
        idempotency_repository: IdempotencyRepository,
        event_writer: EventWriter = enqueue_event,
    ) -> None:
        self._repository = repository
        self._listings = listing_repository
        self._identities = identity_repository
        self._dealers = dealer_repository
        self._policy = authorization_policy
        self._identity_verifications = identity_verification_repository
        self._provider = provider
        self._normalizer = normalizer
        self._hmac_key = hmac_key
        self._hash_version = hash_version
        self._reuse_policy = reuse_policy or OwnershipReusePolicy(
            freshness_days=180, policy_version=1
        )
        self._audit = audit
        self._idempotency = idempotency_repository
        self._event_writer = event_writer

    async def start(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
        request: OwnershipVerificationStartRequest,
        idempotency_key: str,
    ) -> OwnershipVerificationStartResponse:
        keyed = self._normalize_and_key(request)
        request_hash = canonical_request_hash(
            method="POST",
            path=f"/api/v1/listings/{listing_id}/ownership-verification/start",
            payload={
                "expected_listing_version": request.expected_listing_version,
                "jurisdiction": keyed.jurisdiction,
                "registration_hmac": keyed.registration_hmac,
                "vin_hmac": keyed.vin_hmac,
                "chassis_hmac": keyed.chassis_hmac,
                "hash_version": keyed.hash_version,
                "ownership_basis": request.ownership_basis,
            },
        )
        claim = await self._claim_attempt(
            session,
            actor_user_id=actor_user_id,
            listing_id=listing_id,
            request=request,
            keyed=keyed,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        attempt = await self._repository.get_attempt(session, claim.attempt_id)
        listing = await self._listings.get(session, listing_id)
        if attempt is None or listing is None or attempt.owner_user_id != actor_user_id:
            raise self._not_found()
        attempt_status = attempt.status
        canonical_vehicle_id = attempt.canonical_vehicle_id
        ownership_basis = attempt.ownership_basis
        listing_version = listing.version
        await session.rollback()
        if not claim.provider_required:
            return OwnershipVerificationStartResponse.model_validate(
                {
                    "attempt_id": claim.attempt_id,
                    "canonical_vehicle_id": canonical_vehicle_id,
                    "listing_version": listing_version,
                    "status": "verified",
                    "ownership_basis": ownership_basis,
                    "reused": claim.reused,
                    "capture_url": None,
                    "capture_expires_at": None,
                }
            )
        if attempt_status == "manual_review":
            raise AppError(
                status=409,
                code="OWNERSHIP_VERIFICATION_IN_REVIEW",
                title="Verification is under review",
            )
        if attempt_status in OWNERSHIP_TERMINAL_STATUSES:
            raise AppError(
                status=409,
                code="OWNERSHIP_VERIFICATION_CLOSED",
                title="Verification attempt is closed",
            )

        try:
            provider_session = await self._provider.create_session(
                attempt_id=claim.attempt_id,
                owner_user_id=actor_user_id,
                idempotency_reference=str(claim.attempt_id),
            )
        except Exception as exc:
            classification = classify_provider_failure(exc)
            if not classification.retryable:
                await self._finalize_session_failure(
                    session,
                    attempt_id=claim.attempt_id,
                    safe_failure_code=classification.safe_failure_code,
                )
            raise AppError(
                status=503,
                code="OWNERSHIP_VERIFICATION_PROVIDER_UNAVAILABLE",
                title="Ownership verification is temporarily unavailable",
            ) from exc

        await self._finalize_session(
            session,
            attempt_id=claim.attempt_id,
            provider_reference=provider_session.provider_reference,
        )
        refreshed_listing = await self._listings.get(session, listing_id)
        if refreshed_listing is None:
            raise self._not_found()
        return OwnershipVerificationStartResponse.model_validate(
            {
                "attempt_id": claim.attempt_id,
                "canonical_vehicle_id": canonical_vehicle_id,
                "listing_version": refreshed_listing.version,
                "status": "pending",
                "ownership_basis": ownership_basis,
                "reused": False,
                "capture_url": provider_session.capture_url,
                "capture_expires_at": provider_session.capture_expires_at,
            }
        )

    async def status(
        self, session: AsyncSession, *, actor_user_id: UUID, listing_id: UUID
    ) -> OwnershipVerificationStatusResponse:
        listing = await self._listings.get(session, listing_id)
        await self._authorize_listing(session, actor_user_id=actor_user_id, listing=listing)
        if listing is None or listing.canonical_vehicle_id is None:
            raise self._not_found()
        state = await self._require_current_identity(session, actor_user_id)
        canonical = await self._repository.get_canonical(session, listing.canonical_vehicle_id)
        if canonical is None:
            raise self._not_found()
        attempts = await self._repository.list_for_reuse(
            session,
            owner_user_id=actor_user_id,
            canonical_vehicle_id=listing.canonical_vehicle_id,
        )
        selection = self._select_ownership(
            listing=listing,
            canonical=canonical,
            identity_state=state,
            attempts=attempts,
            ownership_basis=None,
            now=datetime.now(UTC),
        )
        if selection.evidence is not None and selection.decision.eligible:
            selected = next(row for row in attempts if row.id == selection.evidence.attempt_id)
            return self._status_response(selected, reused=selection.decision.reused)
        attempt = next((row for row in attempts if row.listing_id == listing.id), None)
        if attempt is None or self._identity_stale(attempt, state):
            raise self._not_found()
        return self._status_response(attempt, reused=False)

    async def apply_provider_result(
        self, session: AsyncSession, result: OwnershipProviderResult
    ) -> OwnershipProviderResultApplyResponse:
        self._validate_result(result)
        async with session.begin():
            attempt = await self._repository.get_by_provider_reference(
                session,
                provider_identifier=self._provider.identifier,
                provider_reference=result.provider_reference,
                for_update=True,
            )
            if attempt is None:
                raise self._result_not_found()
            existing = await self._repository.get_by_result_event(
                session, provider_identifier=self._provider.identifier, event_id=result.event_id
            )
            if existing is not None:
                if existing.id == attempt.id and self._result_matches(attempt, result):
                    return self._apply_response(attempt, "duplicate")
                raise self._result_conflict()

            user = await self._identities.get_user_by_id(
                session, attempt.owner_user_id, for_update=True
            )
            state = await self._identity_verifications.get_state(
                session, attempt.owner_user_id, for_update=True
            )
            listing = await self._listings.get(session, attempt.listing_id, for_update=True)
            canonical = await self._repository.get_canonical(
                session, attempt.canonical_vehicle_id, for_update=True
            )
            stale = (
                attempt.superseded_at is not None
                or user is None
                or user.status != "active"
                or state is None
                or not self._identity_valid(state)
                or self._identity_stale(attempt, state)
                or listing is None
                or listing.owner_user_id != attempt.owner_user_id
                or listing.owner_organization_id is not None
                or listing.canonical_vehicle_id != attempt.canonical_vehicle_id
                or canonical is None
                or canonical.identity_version != attempt.vehicle_identity_version
            )
            try:
                disposition = ownership_result_disposition(
                    attempt_status=attempt.status,
                    attempt_event_id=attempt.provider_result_event_id,
                    result_event_id=result.event_id,
                    result_matches=self._result_matches(attempt, result),
                    stale=stale,
                )
            except ValueError as exc:
                raise self._result_conflict() from exc
            if disposition == "stale":
                attempt.superseded_at = attempt.superseded_at or datetime.now(UTC)
                return self._apply_response(attempt, disposition)
            if disposition == "duplicate":
                return self._apply_response(attempt, disposition)
            if canonical is None:
                raise self._result_not_found()
            try:
                require_ownership_transition(attempt.status, result.status)
            except ValueError as exc:
                raise self._result_conflict() from exc
            self._apply_result(attempt, result, canonical)
            self._record_finalization(session, attempt)
            await session.flush()
            return self._apply_response(attempt, "applied")

    async def _claim_attempt(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
        request: OwnershipVerificationStartRequest,
        keyed: KeyedVehicleIdentity,
        idempotency_key: str,
        request_hash: str,
    ) -> OwnershipClaim:
        now = datetime.now(UTC)
        async with session.begin():
            try:
                reservation = await self._idempotency.reserve(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation=f"ownership_verification.start:{listing_id}",
                    key=idempotency_key,
                    request_hash=request_hash,
                    expires_at=now + timedelta(hours=24),
                )
            except IdempotencyConflictError as exc:
                raise AppError(
                    status=409,
                    code="IDEMPOTENCY_KEY_CONFLICT",
                    title="Idempotency key conflicts",
                ) from exc
            if not reservation.acquired:
                if reservation.replay_body is None:
                    raise AppError(
                        status=409,
                        code="IDEMPOTENCY_IN_PROGRESS",
                        title="Request is in progress",
                    )
                return OwnershipClaim(
                    attempt_id=UUID(str(reservation.replay_body["attempt_id"])),
                    reused=bool(reservation.replay_body.get("reused", False)),
                    provider_required=bool(reservation.replay_body.get("provider_required", True)),
                )

            listing = await self._listings.get(session, listing_id, for_update=True)
            await self._authorize_listing(session, actor_user_id=actor_user_id, listing=listing)
            if listing is None:
                raise self._not_found()
            if listing.lifecycle_status != "draft":
                raise AppError(status=409, code="LISTING_NOT_ACTIVE", title="Listing is not active")
            if listing.version != request.expected_listing_version:
                raise AppError(
                    status=409,
                    code="LISTING_VERSION_CONFLICT",
                    title="Listing version is stale",
                )
            identity_state = await self._require_current_identity(session, actor_user_id, lock=True)
            canonical = await self._resolve_canonical(session, listing=listing, keyed=keyed)
            if listing.canonical_vehicle_id not in {None, canonical.id}:
                raise AppError(
                    status=409,
                    code="VEHICLE_IDENTITY_CONFLICT",
                    title="Vehicle identity conflicts",
                )
            if listing.canonical_vehicle_id is None:
                listing.canonical_vehicle_id = canonical.id
                listing.version += 1

            reuse_attempts = await self._repository.list_for_reuse(
                session,
                owner_user_id=actor_user_id,
                canonical_vehicle_id=canonical.id,
                for_update=True,
            )
            selection = self._select_ownership(
                listing=listing,
                canonical=canonical,
                identity_state=identity_state,
                attempts=reuse_attempts,
                ownership_basis=request.ownership_basis,
                now=now,
            )
            if selection.evidence is not None and selection.decision.eligible:
                selected = next(
                    row for row in reuse_attempts if row.id == selection.evidence.attempt_id
                )
                if selection.decision.reused and not await self._reuse_recorded(
                    session, listing_id=listing.id, attempt_id=selected.id
                ):
                    self._record_reuse(
                        session,
                        actor_user_id=actor_user_id,
                        listing=listing,
                        canonical=canonical,
                        identity_state=identity_state,
                        attempt=selected,
                    )
                await self._idempotency.complete(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation=f"ownership_verification.start:{listing_id}",
                    key=idempotency_key,
                    response_status=201,
                    response_body={
                        "attempt_id": str(selected.id),
                        "reused": selection.decision.reused,
                        "provider_required": False,
                    },
                    resource_type="vehicle_ownership_verification",
                    resource_id=selected.id,
                )
                await session.flush()
                return OwnershipClaim(
                    attempt_id=selected.id,
                    reused=selection.decision.reused,
                    provider_required=False,
                )

            active = await self._repository.get_active(
                session, owner_user_id=actor_user_id, canonical_vehicle_id=canonical.id
            )
            if active is not None and self._identity_stale(active, identity_state):
                active.superseded_at = now
                active = None
            attempt = active or await self._new_attempt(
                session,
                listing=listing,
                canonical=canonical,
                identity_state=identity_state,
                ownership_basis=request.ownership_basis,
                now=now,
            )
            await self._idempotency.complete(
                session,
                scope=f"user:{actor_user_id}",
                operation=f"ownership_verification.start:{listing_id}",
                key=idempotency_key,
                response_status=201,
                response_body={
                    "attempt_id": str(attempt.id),
                    "reused": False,
                    "provider_required": True,
                },
                resource_type="vehicle_ownership_verification",
                resource_id=attempt.id,
            )
            await session.flush()
            return OwnershipClaim(
                attempt_id=attempt.id,
                reused=False,
                provider_required=True,
            )

    async def _new_attempt(
        self,
        session: AsyncSession,
        *,
        listing: Listing,
        canonical: CanonicalVehicle,
        identity_state: UserVerificationState,
        ownership_basis: str,
        now: datetime,
    ) -> VehicleOwnershipVerification:
        if listing.owner_user_id is None:
            raise self._not_found()
        latest = await self._repository.get_latest(
            session,
            owner_user_id=listing.owner_user_id,
            canonical_vehicle_id=canonical.id,
        )
        if latest is not None:
            latest.superseded_at = now
        attempt = VehicleOwnershipVerification(
            id=uuid7(),
            listing_id=listing.id,
            owner_user_id=listing.owner_user_id,
            canonical_vehicle_id=canonical.id,
            attempt_number=await self._repository.next_attempt_number(
                session,
                owner_user_id=listing.owner_user_id,
                canonical_vehicle_id=canonical.id,
            ),
            identity_verification_id=identity_state.current_attempt_id,
            identity_projection_version=identity_state.version,
            vehicle_identity_version=canonical.identity_version,
            hash_version=canonical.hash_version,
            jurisdiction=canonical.jurisdiction or "",
            ownership_basis=ownership_basis,
            material_fingerprint=self._fingerprint(
                canonical=canonical,
                owner_user_id=listing.owner_user_id,
                identity_state=identity_state,
                ownership_basis=ownership_basis,
                provider_result_version=0,
                provider_material_attributes={},
            ),
            provider_identifier=self._provider.identifier,
            status="session_pending",
        )
        session.add(attempt)
        await session.flush()
        return attempt

    async def _resolve_canonical(
        self, session: AsyncSession, *, listing: Listing, keyed: KeyedVehicleIdentity
    ) -> CanonicalVehicle:
        await self._repository.lock_canonical_identity(
            session, registration_hmac=keyed.registration_hmac
        )
        matches = await self._repository.find_canonical(
            session,
            jurisdiction=keyed.jurisdiction,
            hash_version=keyed.hash_version,
            registration_hmac=keyed.registration_hmac,
            vin_hmac=keyed.vin_hmac,
            chassis_hmac=keyed.chassis_hmac,
        )
        unique = {row.id: row for row in matches}
        if len(unique) > 1:
            raise AppError(
                status=409,
                code="VEHICLE_IDENTITY_CONFLICT",
                title="Vehicle identity requires review",
            )
        if not unique:
            canonical = CanonicalVehicle(
                id=uuid7(),
                vehicle_type=listing.vehicle_type,
                jurisdiction=keyed.jurisdiction,
                registration_hmac=keyed.registration_hmac,
                vin_hmac=keyed.vin_hmac,
                chassis_hmac=keyed.chassis_hmac,
                hash_version=keyed.hash_version,
                identity_version=1,
            )
            session.add(canonical)
            await session.flush()
            return canonical
        canonical = next(iter(unique.values()))
        if canonical.vehicle_type != listing.vehicle_type or any(
            current is not None and supplied is not None and current != supplied
            for current, supplied in (
                (canonical.registration_hmac, keyed.registration_hmac),
                (canonical.vin_hmac, keyed.vin_hmac),
                (canonical.chassis_hmac, keyed.chassis_hmac),
            )
        ):
            raise AppError(
                status=409,
                code="VEHICLE_IDENTITY_CONFLICT",
                title="Vehicle identity requires review",
            )
        canonical.jurisdiction = canonical.jurisdiction or keyed.jurisdiction
        canonical.registration_hmac = canonical.registration_hmac or keyed.registration_hmac
        canonical.vin_hmac = canonical.vin_hmac or keyed.vin_hmac
        canonical.chassis_hmac = canonical.chassis_hmac or keyed.chassis_hmac
        return canonical

    async def _finalize_session(
        self, session: AsyncSession, *, attempt_id: UUID, provider_reference: str
    ) -> None:
        async with session.begin():
            attempt = await self._repository.get_attempt(session, attempt_id, for_update=True)
            if attempt is None:
                raise self._not_found()
            if attempt.provider_reference not in {None, provider_reference}:
                raise AppError(
                    status=503,
                    code="OWNERSHIP_VERIFICATION_PROVIDER_UNAVAILABLE",
                    title="Provider unavailable",
                )
            attempt.provider_reference = provider_reference
            if attempt.status == "session_pending":
                attempt.status = "pending"
                self._record_finalization(session, attempt)
            elif attempt.status != "pending":
                raise AppError(
                    status=409,
                    code="OWNERSHIP_VERIFICATION_CLOSED",
                    title="Verification attempt is closed",
                )
            await session.flush()

    async def _finalize_session_failure(
        self, session: AsyncSession, *, attempt_id: UUID, safe_failure_code: str
    ) -> None:
        async with session.begin():
            attempt = await self._repository.get_attempt(session, attempt_id, for_update=True)
            if attempt is None or attempt.status != "session_pending":
                return
            attempt.status = "failed"
            attempt.safe_failure_code = self._safe_failure_code(safe_failure_code)
            self._record_finalization(session, attempt)
            await session.flush()

    async def _authorize_listing(
        self, session: AsyncSession, *, actor_user_id: UUID, listing: Listing | None
    ) -> None:
        user = await self._identities.get_user_by_id(session, actor_user_id)
        if user is None or user.status != "active" or listing is None:
            raise self._not_found()
        if listing.owner_type == "dealer_organization":
            if listing.owner_organization_id is None:
                raise self._not_found()
            organization = await self._dealers.get_organization(
                session, listing.owner_organization_id
            )
            membership = await self._dealers.get_membership_for_user(
                session,
                organization_id=listing.owner_organization_id,
                user_id=actor_user_id,
            )
            if organization is None or membership is None:
                raise self._not_found()
            context = AuthorizationContext(
                user_id=user.id,
                user_status=user.status,
                email_verified=user.email_verified_at is not None,
                phone_verified=user.phone_verified_at is not None,
                organization=OrganizationAccess(
                    organization_id=organization.id,
                    organization_status=organization.status,
                    organization_verification_status=organization.verification_status,
                    membership_id=membership.id,
                    membership_status=membership.status,
                    membership_role=membership.role,
                ),
            )
            if not self._policy.can_manage_owned_resource(
                context,
                owner_user_id=None,
                owner_organization_id=listing.owner_organization_id,
                dealer_permission=DealerPermission.INVENTORY_MANAGE,
            ):
                raise self._not_found()
            raise AppError(
                status=409,
                code="DEALER_OWNERSHIP_VERIFICATION_UNSUPPORTED",
                title="Dealer ownership verification is not supported",
            )
        if (
            listing.owner_type != "user"
            or listing.owner_user_id != actor_user_id
            or listing.owner_organization_id is not None
        ):
            raise self._not_found()

    async def _require_current_identity(
        self, session: AsyncSession, user_id: UUID, *, lock: bool = False
    ) -> UserVerificationState:
        state = await self._identity_verifications.get_state(session, user_id, for_update=lock)
        if state is None or not self._identity_valid(state):
            raise AppError(
                status=409,
                code="CURRENT_IDENTITY_VERIFICATION_REQUIRED",
                title="Current identity verification is required",
            )
        return state

    @staticmethod
    def _identity_valid(state: UserVerificationState) -> bool:
        now = datetime.now(UTC)
        return (
            state.effective_status == "verified"
            and state.verified_at is not None
            and state.expires_at is not None
            and state.expires_at > now
            and state.revoked_at is None
        )

    @staticmethod
    def _identity_stale(
        attempt: VehicleOwnershipVerification, state: UserVerificationState
    ) -> bool:
        return (
            state.current_attempt_id != attempt.identity_verification_id
            or state.version != attempt.identity_projection_version
        )

    def _select_ownership(
        self,
        *,
        listing: Listing,
        canonical: CanonicalVehicle,
        identity_state: UserVerificationState,
        attempts: list[VehicleOwnershipVerification],
        ownership_basis: str | None,
        now: datetime,
    ) -> OwnershipSelection:
        preferred_basis = ownership_basis
        if preferred_basis is None:
            current = next(
                (
                    row
                    for row in attempts
                    if row.listing_id == listing.id and row.status == "verified"
                ),
                None,
            )
            verified = current or next((row for row in attempts if row.status == "verified"), None)
            preferred_basis = verified.ownership_basis if verified else "registered_owner"
        context = OwnershipReuseContext(
            now=now,
            owner_user_id=identity_state.user_id,
            canonical_vehicle_id=canonical.id,
            identity_verification_id=identity_state.current_attempt_id,
            identity_projection_version=identity_state.version,
            vehicle_identity_version=canonical.identity_version,
            vehicle_hash_version=canonical.hash_version,
            vehicle_identity_status=canonical.identity_status,
            ownership_basis=preferred_basis,
            identity_verified=self._identity_valid(identity_state),
            personal_listing=(
                listing.owner_type == "user"
                and listing.owner_user_id == identity_state.user_id
                and listing.owner_organization_id is None
            ),
        )
        evidence = tuple(self._reuse_evidence(row) for row in attempts)
        return self._reuse_policy.select(
            context=context,
            evidence=evidence,
            current_listing_id=listing.id,
        )

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

    def _record_reuse(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing: Listing,
        canonical: CanonicalVehicle,
        identity_state: UserVerificationState,
        attempt: VehicleOwnershipVerification,
    ) -> None:
        changes = {
            "listing_id": str(listing.id),
            "listing_version": listing.version,
            "ownership_verification_id": str(attempt.id),
            "canonical_vehicle_id": str(canonical.id),
            "identity_projection_version": identity_state.version,
            "vehicle_identity_version": canonical.identity_version,
            "reuse_policy_version": self._reuse_policy.policy_version,
            "result": "reused",
        }
        self._audit.record(
            session,
            action="ownership.verification.reused",
            outcome="success",
            resource_type="vehicle_ownership_verification",
            actor_user_id=actor_user_id,
            resource_id=attempt.id,
            changes=changes,
            request_id=get_request_id(),
        )
        self._event_writer(
            session,
            event_type="ownership.verification.reused",
            aggregate_type="vehicle_ownership_verification",
            aggregate_id=attempt.id,
            payload=changes,
        )

    @staticmethod
    async def _reuse_recorded(session: AsyncSession, *, listing_id: UUID, attempt_id: UUID) -> bool:
        existing = await session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.event_type == "ownership.verification.reused",
                OutboxEvent.aggregate_id == attempt_id,
                OutboxEvent.payload["listing_id"].astext == str(listing_id),
            )
        )
        return existing is not None

    def _normalize_and_key(
        self, request: OwnershipVerificationStartRequest
    ) -> KeyedVehicleIdentity:
        try:
            normalized = self._normalizer.normalize(
                jurisdiction=request.jurisdiction,
                registration=request.registration,
                vin=request.vin,
                chassis=request.chassis,
            )
        except VehicleIdentityNormalizerUnavailable as exc:
            raise AppError(
                status=503,
                code="VEHICLE_IDENTITY_UNAVAILABLE",
                title="Vehicle identity is unavailable",
            ) from exc
        except VehicleIdentityInvalid as exc:
            raise AppError(
                status=422,
                code="VEHICLE_IDENTITY_INVALID",
                title="Vehicle identity is invalid",
            ) from exc
        return key_vehicle_identity(normalized, key=self._hmac_key, hash_version=self._hash_version)

    def _apply_result(
        self,
        attempt: VehicleOwnershipVerification,
        result: OwnershipProviderResult,
        canonical: CanonicalVehicle,
    ) -> None:
        attempt.provider_result_event_id = result.event_id
        attempt.provider_result_version = result.result_version
        attempt.status = result.status
        attempt.verified_at = result.verified_at if result.status == "verified" else None
        attempt.expires_at = result.expires_at if result.status == "verified" else None
        attempt.revoked_at = None
        if result.status == "manual_review":
            attempt.safe_failure_code = "MANUAL_REVIEW_REQUIRED"
        elif result.status == "failed":
            attempt.safe_failure_code = self._safe_failure_code(result.safe_failure_code)
        else:
            attempt.safe_failure_code = None
        state = UserVerificationState(
            user_id=attempt.owner_user_id,
            current_attempt_id=attempt.identity_verification_id,
            effective_status="verified",
            version=attempt.identity_projection_version,
        )
        attempt.material_fingerprint = self._fingerprint(
            canonical=canonical,
            owner_user_id=attempt.owner_user_id,
            identity_state=state,
            ownership_basis=attempt.ownership_basis,
            provider_result_version=result.result_version,
            provider_material_attributes=dict(result.material_attributes),
        )

    def _fingerprint(
        self,
        *,
        canonical: CanonicalVehicle,
        owner_user_id: UUID,
        identity_state: UserVerificationState,
        ownership_basis: str,
        provider_result_version: int,
        provider_material_attributes: dict[str, str],
    ) -> str:
        return ownership_material_fingerprint(
            key=self._hmac_key,
            canonical_vehicle_id=canonical.id,
            canonical_identity_version=canonical.identity_version,
            owner_user_id=owner_user_id,
            identity_attempt_id=identity_state.current_attempt_id,
            identity_projection_version=identity_state.version,
            jurisdiction=canonical.jurisdiction or "",
            ownership_basis=ownership_basis,
            registration_hmac=canonical.registration_hmac or "",
            vin_hmac=canonical.vin_hmac,
            chassis_hmac=canonical.chassis_hmac,
            provider_result_version=provider_result_version,
            provider_material_attributes=provider_material_attributes,
        )

    def _record_finalization(
        self, session: AsyncSession, attempt: VehicleOwnershipVerification
    ) -> None:
        changes = {
            "status": attempt.status,
            "ownership_basis": attempt.ownership_basis,
            "failure_code": attempt.safe_failure_code,
            "identity_projection_version": attempt.identity_projection_version,
            "vehicle_identity_version": attempt.vehicle_identity_version,
        }
        self._audit.record(
            session,
            action="vehicle.ownership_verification.state_changed",
            outcome="success",
            resource_type="vehicle_ownership_verification",
            actor_user_id=attempt.owner_user_id,
            resource_id=attempt.id,
            changes=changes,
            request_id=get_request_id(),
        )
        self._event_writer(
            session,
            event_type="vehicle.ownership_verification.state_changed",
            aggregate_type="canonical_vehicle",
            aggregate_id=attempt.canonical_vehicle_id,
            payload={
                "owner_user_id": str(attempt.owner_user_id),
                "listing_id": str(attempt.listing_id),
                "canonical_vehicle_id": str(attempt.canonical_vehicle_id),
                "attempt_id": str(attempt.id),
                "identity_projection_version": attempt.identity_projection_version,
                "vehicle_identity_version": attempt.vehicle_identity_version,
                "status": attempt.status,
                "ownership_basis": attempt.ownership_basis,
                "failure_code": attempt.safe_failure_code,
            },
        )

    @classmethod
    def _validate_result(cls, result: OwnershipProviderResult) -> None:
        invalid_attributes = any(
            not key or len(key) > 64 or len(value) > 200
            for key, value in result.material_attributes.items()
        )
        if (
            not result.provider_reference
            or len(result.provider_reference) > 200
            or not result.event_id
            or len(result.event_id) > 200
            or result.result_version <= 0
            or result.status not in OWNERSHIP_RESULT_STATUSES
            or invalid_attributes
        ):
            raise cls._result_invalid()
        if result.status == "verified" and (
            result.verified_at is None
            or result.expires_at is None
            or result.expires_at <= result.verified_at
            or result.safe_failure_code is not None
        ):
            raise cls._result_invalid()
        if result.status == "failed" and not result.safe_failure_code:
            raise cls._result_invalid()

    @classmethod
    def _result_matches(
        cls, attempt: VehicleOwnershipVerification, result: OwnershipProviderResult
    ) -> bool:
        expected_failure = None
        if result.status == "manual_review":
            expected_failure = "MANUAL_REVIEW_REQUIRED"
        elif result.status == "failed":
            expected_failure = cls._safe_failure_code(result.safe_failure_code)
        return (
            attempt.provider_result_event_id == result.event_id
            and attempt.provider_result_version == result.result_version
            and attempt.status == result.status
            and attempt.verified_at == (result.verified_at if result.status == "verified" else None)
            and attempt.expires_at == (result.expires_at if result.status == "verified" else None)
            and attempt.safe_failure_code == expected_failure
        )

    @staticmethod
    def _safe_failure_code(value: str | None) -> str:
        return value if value in _SAFE_FAILURE_CODES else "VERIFICATION_FAILED"

    @staticmethod
    def _status_response(
        attempt: VehicleOwnershipVerification,
        *,
        reused: bool,
    ) -> OwnershipVerificationStatusResponse:
        return OwnershipVerificationStatusResponse.model_validate(
            {
                "attempt_id": attempt.id,
                "canonical_vehicle_id": attempt.canonical_vehicle_id,
                "status": attempt.status,
                "ownership_basis": attempt.ownership_basis,
                "verified_at": attempt.verified_at,
                "expires_at": attempt.expires_at,
                "revoked_at": attempt.revoked_at,
                "failure_code": attempt.safe_failure_code,
                "reused": reused,
                "updated_at": attempt.updated_at,
            }
        )

    @staticmethod
    def _apply_response(
        attempt: VehicleOwnershipVerification, disposition: str
    ) -> OwnershipProviderResultApplyResponse:
        return OwnershipProviderResultApplyResponse.model_validate(
            {"attempt_id": attempt.id, "status": attempt.status, "disposition": disposition}
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="OWNERSHIP_VERIFICATION_NOT_FOUND",
            title="Verification was not found",
        )

    @staticmethod
    def _result_not_found() -> AppError:
        return AppError(
            status=404,
            code="OWNERSHIP_RESULT_NOT_FOUND",
            title="Verification result target was not found",
        )

    @staticmethod
    def _result_invalid() -> AppError:
        return AppError(
            status=422,
            code="OWNERSHIP_RESULT_INVALID",
            title="Verification result is invalid",
        )

    @staticmethod
    def _result_conflict() -> AppError:
        return AppError(
            status=409,
            code="OWNERSHIP_RESULT_CONFLICT",
            title="Verification result conflicts with state",
        )
