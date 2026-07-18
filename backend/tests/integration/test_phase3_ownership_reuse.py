from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import Environment, Settings
from app.core.database import Database
from app.core.errors import AppError
from app.core.idempotency import IdempotencyRepository, canonical_request_hash
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.catalogue.models import CanonicalVehicle, VehicleMake, VehicleModel, VehicleVariant
from app.modules.dealers.models import DealerMembership, DealerOrganization
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.cursors import ListingCursorCodec
from app.modules.listings.models import CarSpec, Listing, VehicleSpec
from app.modules.listings.readiness import ReadinessPolicy
from app.modules.listings.repository import ListingRepository
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
from app.modules.verification.ownership_provider import (
    DeterministicOwnershipVerificationProvider,
    OwnershipProviderSession,
)
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.ownership_reuse import OwnershipReusePolicy
from app.modules.verification.ownership_schemas import OwnershipVerificationStartRequest
from app.modules.verification.ownership_service import OwnershipVerificationService
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.vehicle_identity import (
    DeterministicVehicleIdentityNormalizer,
    key_vehicle_identity,
)

pytestmark = pytest.mark.integration
HMAC_KEY = b"slice-five-integration-vehicle-key"


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


class CountingProvider:
    identifier = "deterministic"

    def __init__(self) -> None:
        self.calls = 0
        self._delegate = DeterministicOwnershipVerificationProvider()

    async def create_session(
        self, *, attempt_id: Any, owner_user_id: Any, idempotency_reference: str
    ) -> OwnershipProviderSession:
        self.calls += 1
        return await self._delegate.create_session(
            attempt_id=attempt_id,
            owner_user_id=owner_user_id,
            idempotency_reference=idempotency_reference,
        )


@dataclass(slots=True)
class ReuseSeed:
    owner: User
    outsider: User
    source_listing: Listing
    target_listing: Listing
    canonical: CanonicalVehicle
    identity_state: UserVerificationState
    ownership: VehicleOwnershipVerification
    registration: str
    chassis: str


def make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid7(),
        normalized_email=f"reuse-{uuid7()}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-plaintext",
        status="active",
        email_verified_at=now,
        phone_verified_at=now,
        password_changed_at=now,
    )


async def seed_reusable_relationship(
    database: Database,
    *,
    ownership_age_days: int = 10,
    provider_expiry_days: int = 365,
) -> ReuseSeed:
    owner = make_user()
    outsider = make_user()
    source_listing = Listing(
        id=uuid7(),
        owner_type="user",
        owner_user_id=owner.id,
        created_by_user_id=owner.id,
        vehicle_type="car",
        lifecycle_status="draft",
        version=1,
    )
    target_listing = Listing(
        id=uuid7(),
        owner_type="user",
        owner_user_id=owner.id,
        created_by_user_id=owner.id,
        vehicle_type="car",
        lifecycle_status="draft",
        version=1,
    )
    suffix = target_listing.id.hex[-8:].upper()
    registration = f"KL07{suffix}"
    chassis = f"CH{suffix}XYZ"
    normalized = DeterministicVehicleIdentityNormalizer().normalize(
        jurisdiction="IN-KL",
        registration=registration,
        vin=None,
        chassis=chassis,
    )
    keyed = key_vehicle_identity(normalized, key=HMAC_KEY, hash_version=1)
    make = VehicleMake(
        id=uuid7(), name=f"Reuse {suffix}", normalized_name=f"reuse-{suffix}", vehicle_type="car"
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
        jurisdiction=keyed.jurisdiction,
        registration_hmac=keyed.registration_hmac,
        vin_hmac=keyed.vin_hmac,
        chassis_hmac=keyed.chassis_hmac,
        hash_version=1,
        identity_version=1,
        identity_status="active",
    )
    now = datetime.now(UTC)
    identity = IdentityVerification(
        id=uuid7(),
        user_id=owner.id,
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
        user_id=owner.id,
        current_attempt_id=identity.id,
        effective_status="verified",
        assurance_level="standard",
        verified_at=now,
        expires_at=now + timedelta(days=365),
        version=1,
    )
    ownership = VehicleOwnershipVerification(
        id=uuid7(),
        listing_id=source_listing.id,
        owner_user_id=owner.id,
        canonical_vehicle_id=canonical.id,
        attempt_number=1,
        identity_verification_id=identity.id,
        identity_projection_version=1,
        vehicle_identity_version=1,
        hash_version=1,
        jurisdiction="IN-KL",
        ownership_basis="registered_owner",
        material_fingerprint="a" * 64,
        provider_identifier="deterministic",
        provider_reference=f"ownership:{uuid7()}",
        provider_result_event_id=f"ownership-result:{uuid7()}",
        provider_result_version=1,
        status="verified",
        verified_at=now - timedelta(days=ownership_age_days),
        expires_at=now + timedelta(days=provider_expiry_days),
    )
    source_listing.variant_id = variant.id
    source_listing.canonical_vehicle_id = canonical.id
    target_listing.variant_id = variant.id
    target_listing.canonical_vehicle_id = canonical.id
    target_listing.title = "Reusable ownership listing"
    target_listing.description = "A complete private vehicle listing awaiting moderation."
    target_listing.asking_price = Decimal("125000.00")
    media = ListingMedia(
        id=uuid7(),
        listing_id=target_listing.id,
        created_by_user_id=owner.id,
        object_key=f"private/{target_listing.id}/sanitized.jpg",
        expected_content_type="image/jpeg",
        expected_size_bytes=1024,
        expected_checksum_sha256="b" * 64,
        sort_order=0,
        status="moderation_pending",
        processing_version=1,
        expires_at=now + timedelta(days=1),
        completed_at=now,
        processed_at=now,
    )
    async with database.session_factory() as session, session.begin():
        session.add_all([owner, outsider])
        await session.flush()
        session.add(make)
        await session.flush()
        session.add(model)
        await session.flush()
        session.add(variant)
        await session.flush()
        session.add(canonical)
        await session.flush()
        session.add_all([source_listing, target_listing, identity])
        await session.flush()
        session.add_all(
            [
                identity_state,
                ownership,
                SellerProfile(
                    user_id=owner.id,
                    status="active",
                    readiness_state="ready",
                    activated_at=now,
                ),
                VehicleSpec(
                    listing_id=target_listing.id,
                    manufacture_year=2024,
                    odometer_km=1000,
                    fuel_type="petrol",
                    transmission="manual",
                    ownership_count=1,
                    colour="black",
                    condition="excellent",
                ),
                CarSpec(
                    listing_id=target_listing.id,
                    body_type="sedan",
                    seats=5,
                    engine_cc=1500,
                    drivetrain="fwd",
                ),
                media,
            ]
        )
        await LocationRepository().upsert(
            session,
            listing_id=target_listing.id,
            latitude=10.0,
            longitude=76.0,
            locality="Private locality",
            coarse_area="Coarse area",
            visibility="approximate",
            public_address_id=None,
        )
    return ReuseSeed(
        owner=owner,
        outsider=outsider,
        source_listing=source_listing,
        target_listing=target_listing,
        canonical=canonical,
        identity_state=identity_state,
        ownership=ownership,
        registration=registration,
        chassis=chassis,
    )


def ownership_service(provider: CountingProvider) -> OwnershipVerificationService:
    return OwnershipVerificationService(
        repository=OwnershipVerificationRepository(),
        listing_repository=ListingRepository(),
        identity_repository=IdentityRepository(),
        dealer_repository=DealerRepository(),
        authorization_policy=AuthorizationPolicy(),
        identity_verification_repository=VerificationRepository(),
        provider=provider,
        normalizer=DeterministicVehicleIdentityNormalizer(),
        hmac_key=HMAC_KEY,
        hash_version=1,
        reuse_policy=OwnershipReusePolicy(freshness_days=180, policy_version=1),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )


def submission_service() -> ListingSubmissionService:
    listing_repository = ListingRepository()
    return ListingSubmissionService(
        listing_service=ListingService(
            repository=listing_repository,
            identity_repository=IdentityRepository(),
            dealer_repository=DealerRepository(),
            policy=AuthorizationPolicy(),
            audit=AuditRecorder(),
            idempotency_repository=IdempotencyRepository(),
            cursor_codec=ListingCursorCodec("slice-five-cursor-signing-key"),
        ),
        listing_repository=listing_repository,
        submission_repository=ListingSubmissionRepository(),
        profile_repository=ProfileRepository(),
        location_repository=LocationRepository(),
        media_repository=MediaRepository(),
        verification_repository=VerificationRepository(),
        ownership_repository=OwnershipVerificationRepository(),
        ownership_reuse_policy=OwnershipReusePolicy(freshness_days=180, policy_version=1),
        policy=ReadinessPolicy(),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )


def start_request(seed: ReuseSeed) -> OwnershipVerificationStartRequest:
    return OwnershipVerificationStartRequest(
        expected_listing_version=1,
        jurisdiction="IN-KL",
        registration=seed.registration,
        chassis=seed.chassis,
        ownership_basis="registered_owner",
    )


async def start(
    database: Database,
    service: OwnershipVerificationService,
    seed: ReuseSeed,
    *,
    key: str,
    actor: User | None = None,
    listing: Listing | None = None,
) -> Any:
    async with database.session_factory() as session:
        return await service.start(
            session,
            actor_user_id=(actor or seed.owner).id,
            listing_id=(listing or seed.target_listing).id,
            request=start_request(seed),
            idempotency_key=key,
        )


@pytest.mark.asyncio
async def test_reuse_start_status_idempotency_concurrency_and_privacy() -> None:
    database = Database.create(integration_settings())
    provider = CountingProvider()
    try:
        seed = await seed_reusable_relationship(database)
        service = ownership_service(provider)
        original_times = (seed.ownership.verified_at, seed.ownership.expires_at)
        key = f"ownership-reuse-{uuid7()}"
        first = await start(database, service, seed, key=key)
        replay = await start(database, service, seed, key=key)
        assert first == replay
        assert first.attempt_id == seed.ownership.id
        assert first.status == "verified" and first.reused
        assert first.capture_url is None and provider.calls == 0

        concurrent = await asyncio.gather(
            start(database, service, seed, key=f"ownership-reuse-a-{uuid7()}"),
            start(database, service, seed, key=f"ownership-reuse-b-{uuid7()}"),
        )
        assert all(
            result.attempt_id == seed.ownership.id and result.reused for result in concurrent
        )
        async with database.session_factory() as session:
            status = await service.status(
                session,
                actor_user_id=seed.owner.id,
                listing_id=seed.target_listing.id,
            )
            rows = int(
                await session.scalar(
                    select(func.count())
                    .select_from(VehicleOwnershipVerification)
                    .where(
                        VehicleOwnershipVerification.owner_user_id == seed.owner.id,
                        VehicleOwnershipVerification.canonical_vehicle_id == seed.canonical.id,
                    )
                )
                or 0
            )
            audits = (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == "ownership.verification.reused",
                        AuditLog.changes["listing_id"].astext == str(seed.target_listing.id),
                    )
                )
            ).all()
            events = (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "ownership.verification.reused",
                        OutboxEvent.payload["listing_id"].astext == str(seed.target_listing.id),
                    )
                )
            ).all()
            persisted = await session.get(VehicleOwnershipVerification, seed.ownership.id)
        assert status.reused and status.attempt_id == seed.ownership.id
        assert rows == 1 and provider.calls == 0
        assert len(audits) == len(events) == 1
        assert persisted is not None
        assert (persisted.verified_at, persisted.expires_at) == original_times
        serialized = json.dumps(
            {
                "start": first.model_dump(mode="json"),
                "status": status.model_dump(mode="json"),
                "audits": [audit.changes for audit in audits],
                "events": [event.payload for event in events],
            }
        ).casefold()
        for forbidden in (
            seed.registration.casefold(),
            seed.chassis.casefold(),
            "registration_hmac",
            "chassis_hmac",
            "material_fingerprint",
            "provider_reference",
            "document_reference",
        ):
            assert forbidden not in serialized
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_submission_and_readiness_record_reused_source_without_changing_proof() -> None:
    database = Database.create(integration_settings())
    try:
        seed = await seed_reusable_relationship(database)
        service = submission_service()
        original_times = (seed.ownership.verified_at, seed.ownership.expires_at)
        async with database.session_factory() as session:
            readiness = await service.readiness(
                session,
                actor_user_id=seed.owner.id,
                listing_id=seed.target_listing.id,
            )
        ownership_gate = next(
            gate for gate in readiness.gates if gate.name == "ownership_verification"
        )
        assert ownership_gate.state == "ready"
        assert readiness.ownership_reused and not readiness.publishable

        request = ListingSubmissionRequest(expected_version=1)
        path = f"/api/v1/listings/{seed.target_listing.id}/submit"
        async with database.session_factory() as session:
            submitted = await service.submit(
                session,
                actor_user_id=seed.owner.id,
                listing_id=seed.target_listing.id,
                request=request,
                idempotency_key=f"listing-reuse-{uuid7()}",
                request_hash=canonical_request_hash(
                    method="POST", path=path, payload={"expected_version": 1}
                ),
            )
        assert submitted.ownership_reused
        assert submitted.submission_status == "moderation_pending"
        assert not submitted.publishable
        async with database.session_factory() as session:
            attempt = await session.scalar(
                select(ListingSubmissionAttempt).where(
                    ListingSubmissionAttempt.id == submitted.submission_attempt_id
                )
            )
            persisted = await session.get(VehicleOwnershipVerification, seed.ownership.id)
        assert attempt is not None and persisted is not None
        assert attempt.ownership_verification_id == seed.ownership.id
        assert attempt.ownership_reused
        assert attempt.ownership_reuse_policy_version == 1
        assert original_times[0] is not None and original_times[1] is not None
        assert attempt.ownership_effective_expires_at == min(
            original_times[1], original_times[0] + timedelta(days=180)
        )
        assert (persisted.verified_at, persisted.expires_at) == original_times
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age_days", "provider_expiry_days"),
    [pytest.param(10, -1, id="provider-expired"), pytest.param(181, 365, id="policy-expired")],
)
async def test_provider_and_policy_expiry_block_reuse(
    age_days: int, provider_expiry_days: int
) -> None:
    database = Database.create(integration_settings())
    provider = CountingProvider()
    try:
        seed = await seed_reusable_relationship(
            database,
            ownership_age_days=age_days,
            provider_expiry_days=provider_expiry_days,
        )
        result = await start(
            database,
            ownership_service(provider),
            seed,
            key=f"ownership-expired-{uuid7()}",
        )
        assert not result.reused
        assert provider.calls == 1
        async with database.session_factory() as session:
            rows = int(
                await session.scalar(
                    select(func.count())
                    .select_from(VehicleOwnershipVerification)
                    .where(
                        VehicleOwnershipVerification.owner_user_id == seed.owner.id,
                        VehicleOwnershipVerification.canonical_vehicle_id == seed.canonical.id,
                    )
                )
                or 0
            )
        assert rows == 2
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_identity_and_vehicle_version_changes_immediately_invalidate_readiness() -> None:
    database = Database.create(integration_settings())
    try:
        seed = await seed_reusable_relationship(database)
        service = submission_service()
        async with database.session_factory() as session, session.begin():
            state = await session.get(UserVerificationState, seed.owner.id, with_for_update=True)
            assert state is not None
            state.version += 1
        async with database.session_factory() as session:
            identity_changed = await service.readiness(
                session, actor_user_id=seed.owner.id, listing_id=seed.target_listing.id
            )
        identity_gate = next(
            gate for gate in identity_changed.gates if gate.name == "ownership_verification"
        )
        assert identity_gate.state == "blocked"
        assert not identity_changed.ownership_reused

        async with database.session_factory() as session, session.begin():
            state = await session.get(UserVerificationState, seed.owner.id, with_for_update=True)
            canonical = await session.get(CanonicalVehicle, seed.canonical.id, with_for_update=True)
            assert state is not None and canonical is not None
            state.version = 1
            canonical.identity_version += 1
        async with database.session_factory() as session:
            vehicle_changed = await service.readiness(
                session, actor_user_id=seed.owner.id, listing_id=seed.target_listing.id
            )
        vehicle_gate = next(
            gate for gate in vehicle_changed.gates if gate.name == "ownership_verification"
        )
        assert vehicle_gate.state == "blocked"
        assert not vehicle_changed.ownership_reused
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_cross_owner_and_dealer_authorization_remain_fail_closed() -> None:
    database = Database.create(integration_settings())
    provider = CountingProvider()
    try:
        seed = await seed_reusable_relationship(database)
        service = ownership_service(provider)
        with pytest.raises(AppError) as denied:
            await start(
                database,
                service,
                seed,
                key=f"ownership-outsider-{uuid7()}",
                actor=seed.outsider,
            )
        assert denied.value.code == "OWNERSHIP_VERIFICATION_NOT_FOUND"

        organization = DealerOrganization(
            id=uuid7(),
            legal_name=f"Reuse Dealer {uuid7()}",
            display_name="Reuse Dealer",
            status="active",
            verification_status="verified",
            created_by_user_id=seed.owner.id,
        )
        dealer_listing = Listing(
            id=uuid7(),
            owner_type="dealer_organization",
            owner_organization_id=organization.id,
            created_by_user_id=seed.owner.id,
            vehicle_type="car",
            lifecycle_status="draft",
            version=1,
            variant_id=seed.source_listing.variant_id,
            canonical_vehicle_id=seed.canonical.id,
        )
        async with database.session_factory() as session, session.begin():
            session.add(organization)
            await session.flush()
            session.add_all(
                [
                    DealerMembership(
                        id=uuid7(),
                        organization_id=organization.id,
                        user_id=seed.owner.id,
                        role="owner",
                        status="active",
                        accepted_at=datetime.now(UTC),
                        version=1,
                    ),
                    dealer_listing,
                ]
            )
        with pytest.raises(AppError) as unsupported:
            await start(
                database,
                service,
                seed,
                key=f"ownership-dealer-{uuid7()}",
                listing=dealer_listing,
            )
        assert unsupported.value.code == "DEALER_OWNERSHIP_VERIFICATION_UNSUPPORTED"
        assert provider.calls == 0
    finally:
        await database.close()
