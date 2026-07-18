from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Never

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import Environment, Settings
from app.core.database import Database, get_session
from app.core.errors import AppError
from app.core.errors.handlers import install_exception_handlers
from app.core.idempotency import IdempotencyRepository, canonical_request_hash
from app.core.idempotency.models import IdempotencyRecord
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.catalogue.models import CanonicalVehicle, VehicleMake, VehicleModel, VehicleVariant
from app.modules.dealers.models import DealerMembership, DealerOrganization
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.cursors import ListingCursorCodec
from app.modules.listings.dependencies import get_listing_submission_service
from app.modules.listings.models import CarSpec, Listing, VehicleSpec
from app.modules.listings.readiness import ReadinessPolicy
from app.modules.listings.repository import ListingRepository
from app.modules.listings.router import router as listing_router
from app.modules.listings.service import ListingService
from app.modules.listings.submission_models import ListingSubmissionAttempt
from app.modules.listings.submission_repository import ListingSubmissionRepository
from app.modules.listings.submission_schemas import ListingSubmissionRequest
from app.modules.listings.submission_service import ListingSubmissionService
from app.modules.locations.repository import LocationRepository
from app.modules.media.models import ListingMedia
from app.modules.media.repository import MediaRepository
from app.modules.profiles.models import SellerProfile
from app.modules.profiles.repository import ProfileRepository
from app.modules.verification.models import IdentityVerification, UserVerificationState
from app.modules.verification.ownership_models import VehicleOwnershipVerification
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.repository import VerificationRepository

pytestmark = pytest.mark.integration


def integration_settings() -> Settings:
    database_url = os.getenv("WHEELMATCH_TEST_DATABASE_URL")
    redis_url = os.getenv("WHEELMATCH_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL/Redis test endpoints are not configured")
    return Settings(
        _env_file=None,
        environment=Environment.TEST,
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
    )


def make_user(*, status: str = "active") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid7(),
        normalized_email=f"submission-{uuid7()}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-plaintext",
        status=status,
        email_verified_at=now,
        phone_verified_at=now,
        password_changed_at=now,
    )


def build_service(*, event_writer: Any = None) -> ListingSubmissionService:
    listing_repository = ListingRepository()
    kwargs: dict[str, Any] = {}
    if event_writer is not None:
        kwargs["event_writer"] = event_writer
    return ListingSubmissionService(
        listing_service=ListingService(
            repository=listing_repository,
            identity_repository=IdentityRepository(),
            dealer_repository=DealerRepository(),
            policy=AuthorizationPolicy(),
            audit=AuditRecorder(),
            idempotency_repository=IdempotencyRepository(),
            cursor_codec=ListingCursorCodec("integration-cursor-signing-key"),
        ),
        listing_repository=listing_repository,
        submission_repository=ListingSubmissionRepository(),
        profile_repository=ProfileRepository(),
        location_repository=LocationRepository(),
        media_repository=MediaRepository(),
        verification_repository=VerificationRepository(),
        ownership_repository=OwnershipVerificationRepository(),
        policy=ReadinessPolicy(),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
        **kwargs,
    )


async def add_personal_listing(
    database: Database, *, ready: bool
) -> tuple[User, Listing, ListingMedia | None]:
    user = make_user()
    listing = Listing(
        id=uuid7(),
        owner_type="user",
        owner_user_id=user.id,
        created_by_user_id=user.id,
        vehicle_type="car",
        lifecycle_status="draft",
        version=1,
    )
    async with database.session_factory() as session, session.begin():
        session.add_all([user, listing])
        await session.flush()
        if not ready:
            return user, listing, None

        suffix = listing.id.hex[-10:]
        make = VehicleMake(
            id=uuid7(),
            name=f"Make {suffix}",
            normalized_name=f"make-{suffix}",
            vehicle_type="car",
        )
        model = VehicleModel(
            id=uuid7(),
            make_id=make.id,
            name=f"Model {suffix}",
            normalized_name=f"model-{suffix}",
            vehicle_type="car",
        )
        variant = VehicleVariant(
            id=uuid7(),
            model_id=model.id,
            name=f"Variant {suffix}",
            normalized_name=f"variant-{suffix}",
        )
        canonical = CanonicalVehicle(
            id=uuid7(),
            vehicle_type="car",
            variant_id=variant.id,
            jurisdiction="IN-KL",
            registration_hmac=(suffix * 7)[:64],
            hash_version=1,
            identity_version=1,
        )
        session.add(make)
        await session.flush()
        session.add(model)
        await session.flush()
        session.add(variant)
        await session.flush()
        session.add(canonical)
        await session.flush()
        now = datetime.now(UTC)
        identity_attempt = IdentityVerification(
            id=uuid7(),
            user_id=user.id,
            attempt_number=1,
            provider_identifier="deterministic",
            provider_reference=f"identity:{uuid7()}",
            provider_result_event_id=f"identity-result:{uuid7()}",
            status="verified",
            assurance_level="standard",
            verified_at=now,
            expires_at=now + timedelta(days=365),
        )
        identity_state = UserVerificationState(
            user_id=user.id,
            current_attempt_id=identity_attempt.id,
            effective_status="verified",
            assurance_level="standard",
            verified_at=now,
            expires_at=now + timedelta(days=365),
            version=1,
        )
        session.add(identity_attempt)
        await session.flush()
        ownership = VehicleOwnershipVerification(
            id=uuid7(),
            listing_id=listing.id,
            owner_user_id=user.id,
            canonical_vehicle_id=canonical.id,
            attempt_number=1,
            identity_verification_id=identity_attempt.id,
            identity_projection_version=1,
            vehicle_identity_version=1,
            hash_version=1,
            jurisdiction="IN-KL",
            ownership_basis="registered_owner",
            material_fingerprint="f" * 64,
            provider_identifier="deterministic",
            provider_reference=f"ownership:{uuid7()}",
            provider_result_event_id=f"ownership-result:{uuid7()}",
            provider_result_version=1,
            status="verified",
            verified_at=now,
            expires_at=now + timedelta(days=180),
        )
        media = ListingMedia(
            id=uuid7(),
            listing_id=listing.id,
            created_by_user_id=user.id,
            object_key=f"private/{listing.id}/sanitized.jpg",
            expected_content_type="image/jpeg",
            expected_size_bytes=1024,
            expected_checksum_sha256="a" * 64,
            sort_order=0,
            status="moderation_pending",
            processing_version=1,
            expires_at=now + timedelta(days=1),
            completed_at=now,
            processed_at=now,
        )
        listing.variant_id = variant.id
        listing.canonical_vehicle_id = canonical.id
        listing.title = "Complete private listing"
        listing.description = "A complete private listing ready for moderation."
        listing.asking_price = Decimal("125000.00")
        session.add_all(
            [
                SellerProfile(
                    user_id=user.id,
                    status="active",
                    readiness_state="ready",
                    activated_at=now,
                ),
                VehicleSpec(
                    listing_id=listing.id,
                    manufacture_year=2024,
                    odometer_km=1000,
                    fuel_type="petrol",
                    transmission="manual",
                    ownership_count=1,
                    colour="black",
                    condition="excellent",
                ),
                CarSpec(
                    listing_id=listing.id,
                    body_type="sedan",
                    seats=5,
                    engine_cc=1500,
                    drivetrain="fwd",
                ),
                identity_state,
                ownership,
                media,
            ]
        )
        await LocationRepository().upsert(
            session,
            listing_id=listing.id,
            latitude=10.0,
            longitude=76.0,
            locality="Private locality",
            coarse_area="Coarse area",
            visibility="approximate",
            public_address_id=None,
        )
    return user, listing, media


async def add_dealer_listing(database: Database, operator: User) -> Listing:
    organization = DealerOrganization(
        id=uuid7(),
        legal_name=f"Submission Org {uuid7()}",
        display_name="Submission Org",
        status="active",
        verification_status="verified",
        created_by_user_id=operator.id,
    )
    listing = Listing(
        id=uuid7(),
        owner_type="dealer_organization",
        owner_organization_id=organization.id,
        created_by_user_id=operator.id,
        vehicle_type="car",
        lifecycle_status="draft",
        version=1,
    )
    async with database.session_factory() as session, session.begin():
        session.add(organization)
        await session.flush()
        session.add_all(
            [
                DealerMembership(
                    id=uuid7(),
                    organization_id=organization.id,
                    user_id=operator.id,
                    role="owner",
                    status="active",
                    accepted_at=datetime.now(UTC),
                    version=1,
                ),
                listing,
            ]
        )
    return listing


def submission_hash(listing: Listing, expected_version: int) -> str:
    return canonical_request_hash(
        method="POST",
        path=f"/api/v1/listings/{listing.id}/submit",
        payload={"expected_version": expected_version},
    )


async def submit(
    database: Database,
    service: ListingSubmissionService,
    user: User,
    listing: Listing,
    *,
    key: str,
    expected_version: int = 1,
) -> Any:
    async with database.session_factory() as session:
        return await service.submit(
            session,
            actor_user_id=user.id,
            listing_id=listing.id,
            request=ListingSubmissionRequest(expected_version=expected_version),
            idempotency_key=key,
            request_hash=submission_hash(listing, expected_version),
        )


@pytest.mark.asyncio
async def test_personal_submission_is_idempotent_versioned_and_atomic() -> None:
    database = Database.create(integration_settings())
    try:
        owner, listing, _ = await add_personal_listing(database, ready=True)
        service = build_service()
        key = f"listing-submit-{uuid7()}"
        first = await submit(database, service, owner, listing, key=key)
        replay = await submit(database, service, owner, listing, key=key)
        assert first == replay
        assert first.submission_status == "moderation_pending"
        assert first.moderation_status == "pending"
        assert not first.publishable

        with pytest.raises(AppError) as conflict:
            await submit(database, service, owner, listing, key=key, expected_version=2)
        assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"
        with pytest.raises(AppError) as stale:
            await submit(
                database,
                service,
                owner,
                listing,
                key=f"listing-stale-{uuid7()}",
                expected_version=2,
            )
        assert stale.value.code == "LISTING_VERSION_CHANGED"

        async with database.session_factory() as session:
            attempts = await session.scalar(
                select(func.count())
                .select_from(ListingSubmissionAttempt)
                .where(ListingSubmissionAttempt.listing_id == listing.id)
            )
            audits = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "listing.submission.recorded",
                    AuditLog.changes["listing_id"].astext == str(listing.id),
                )
            )
            events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "listing.moderation.requested",
                    OutboxEvent.aggregate_id == listing.id,
                )
            )
            keys = await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.operation == "listing.submit",
                    IdempotencyRecord.resource_id == first.submission_attempt_id,
                )
            )
        assert (attempts, audits, events, keys) == (1, 1, 1, 1)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_missing_sources_and_authorization_return_only_safe_results() -> None:
    database = Database.create(integration_settings())
    try:
        owner, listing, _ = await add_personal_listing(database, ready=False)
        outsider, _, _ = await add_personal_listing(database, ready=False)
        dealer = await add_dealer_listing(database, owner)
        service = build_service()
        async with database.session_factory() as session:
            readiness = await service.readiness(
                session, actor_user_id=owner.id, listing_id=listing.id
            )
        codes = {gate.code for gate in readiness.gates if gate.code}
        assert {
            "SELLER_RESTRICTED",
            "LISTING_DETAILS_INCOMPLETE",
            "LISTING_LOCATION_REQUIRED",
            "IDENTITY_VERIFICATION_REQUIRED",
            "OWNERSHIP_VERIFICATION_REQUIRED",
            "MEDIA_PROCESSING_INCOMPLETE",
        }.issubset(codes)
        with pytest.raises(AppError) as denied:
            async with database.session_factory() as session:
                await service.readiness(session, actor_user_id=outsider.id, listing_id=listing.id)
        assert denied.value.code == "LISTING_NOT_FOUND"
        with pytest.raises(AppError) as unsupported:
            async with database.session_factory() as session:
                await service.readiness(session, actor_user_id=owner.id, listing_id=dealer.id)
        assert unsupported.value.code == "DEALER_SUBMISSION_NOT_IMPLEMENTED"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_stale_source_state_and_api_response_remain_private() -> None:
    database = Database.create(integration_settings())
    try:
        owner, listing, media = await add_personal_listing(database, ready=True)
        assert media is not None
        service = build_service()
        await submit(
            database,
            service,
            owner,
            listing,
            key=f"listing-private-{uuid7()}",
        )
        async with database.session_factory() as session, session.begin():
            current_media = await session.get(ListingMedia, media.id, with_for_update=True)
            current_listing = await session.get(Listing, listing.id, with_for_update=True)
            assert current_media is not None and current_listing is not None
            current_media.processing_version += 1
            current_listing.version += 1

        app = FastAPI()
        app.state.settings = integration_settings()
        install_exception_handlers(app)
        app.include_router(listing_router)

        async def session_override() -> Any:
            async with database.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_listing_submission_service] = lambda: service
        app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(user_id=owner.id)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(f"/api/v1/listings/{listing.id}/publication-readiness")
        assert response.status_code == 200
        body = response.json()
        gates = {gate["name"]: gate for gate in body["gates"]}
        assert gates["listing_details"]["code"] == "LISTING_VERSION_CHANGED"
        assert gates["media_moderation_queue"]["code"] == "LISTING_VERSION_CHANGED"
        assert not body["publishable"]
        serialized = json.dumps(body)
        for forbidden in (
            "exact_point",
            "coordinates",
            "provider_reference",
            "provider_result_event_id",
            "material_fingerprint",
            "object_key",
            "checksum",
            "url",
        ):
            assert forbidden not in serialized.casefold()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_submission_audit_idempotency_and_outbox_rollback_together() -> None:
    database = Database.create(integration_settings())

    def fail_event(*_args: Any, **_kwargs: Any) -> Never:
        raise RuntimeError("forced event failure")

    try:
        owner, listing, _ = await add_personal_listing(database, ready=True)
        key = f"listing-rollback-{uuid7()}"
        with pytest.raises(RuntimeError, match="forced event failure"):
            await submit(database, build_service(event_writer=fail_event), owner, listing, key=key)
        async with database.session_factory() as session:
            attempts = await session.scalar(
                select(func.count())
                .select_from(ListingSubmissionAttempt)
                .where(ListingSubmissionAttempt.listing_id == listing.id)
            )
            audits = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "listing.submission.recorded",
                    AuditLog.changes["listing_id"].astext == str(listing.id),
                )
            )
            events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == listing.id,
                    OutboxEvent.event_type == "listing.moderation.requested",
                )
            )
            keys = await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.operation == "listing.submit",
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        assert (attempts, audits, events, keys) == (0, 0, 0, 0)
    finally:
        await database.close()
