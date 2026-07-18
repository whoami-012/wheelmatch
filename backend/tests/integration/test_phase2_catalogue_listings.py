from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import Environment, Settings
from app.core.database import Database
from app.core.idempotency import IdempotencyRepository, canonical_request_hash
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.catalogue.models import VehicleMake, VehicleModel, VehicleVariant
from app.modules.catalogue.repository import CatalogueRepository
from app.modules.catalogue.service import CatalogueService
from app.modules.dealers.models import DealerMembership, DealerOrganization
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.cursors import ListingCursorCodec
from app.modules.listings.repository import ListingRepository
from app.modules.listings.schemas import (
    CarSpecInput,
    DealerOwnerContext,
    ListingCreateRequest,
    ListingPrivateResponse,
    ListingUpdateRequest,
    PersonalOwnerContext,
    VehicleSpecInput,
)
from app.modules.listings.service import ListingService
from app.modules.locations.repository import LocationRepository
from app.modules.locations.schemas import ListingLocationWriteRequest
from app.modules.locations.service import LocationService
from app.modules.media.repository import MediaRepository
from app.modules.media.schemas import MediaCompleteRequest, MediaUploadIntentRequest
from app.modules.media.service import MediaService
from app.modules.media.storage import MediaStorage

pytestmark = pytest.mark.integration


def integration_settings() -> Settings:
    database_url = os.getenv("WHEELMATCH_TEST_DATABASE_URL")
    redis_url = os.getenv("WHEELMATCH_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("integration service URLs are not configured")
    return Settings(
        environment=Environment.TEST,
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        aws_endpoint_url=os.getenv("WHEELMATCH_TEST_AWS_ENDPOINT_URL"),
        s3_media_bucket=os.getenv("WHEELMATCH_TEST_S3_MEDIA_BUCKET", "wheelmatch-media-local"),
    )


def new_user(*, verified: bool = True) -> User:
    now = datetime.now(UTC) if verified else None
    return User(
        id=uuid7(),
        normalized_email=f"phase2-{uuid7()}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-plaintext",
        status="active",
        email_verified_at=now,
        phone_verified_at=now,
        password_changed_at=datetime.now(UTC),
    )


def listing_service() -> ListingService:
    return ListingService(
        repository=ListingRepository(),
        identity_repository=IdentityRepository(),
        dealer_repository=DealerRepository(),
        policy=AuthorizationPolicy(),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
        cursor_codec=ListingCursorCodec("phase2-integration-cursor-key"),
    )


def create_request_hash(request: ListingCreateRequest) -> str:
    return canonical_request_hash(
        method="POST", path="/api/v1/listings", payload=request.model_dump(mode="json")
    )


async def create_personal_listing(
    database: Database, service: ListingService, owner: User, *, vehicle_type: str = "car"
) -> ListingPrivateResponse:
    request = ListingCreateRequest(
        owner_context=PersonalOwnerContext(type="personal"), vehicle_type=vehicle_type
    )
    async with database.session_factory() as session:
        return await service.create(
            session,
            actor_user_id=owner.id,
            request=request,
            idempotency_key=f"phase2-listing-{uuid7()}",
            request_hash=create_request_hash(request),
        )


@pytest.mark.asyncio
async def test_catalogue_hierarchy_uniqueness_and_bounded_search() -> None:
    database = Database.create(integration_settings())
    suffix = str(uuid7())
    make_name = f"BMW {suffix}"
    make = VehicleMake(
        id=uuid7(),
        name=make_name,
        normalized_name=make_name.casefold(),
        vehicle_type="both",
    )
    model = VehicleModel(
        id=uuid7(),
        make_id=make.id,
        name="M4",
        normalized_name="m4",
        vehicle_type="car",
    )
    variants = [
        VehicleVariant(
            id=uuid7(),
            model_id=model.id,
            name=name,
            normalized_name=name.casefold(),
        )
        for name in ("Competition", "CS")
    ]
    try:
        async with database.session_factory() as session, session.begin():
            session.add(make)
            await session.flush()
            session.add(model)
            await session.flush()
            session.add_all(variants)

        service = CatalogueService(CatalogueRepository())
        async with database.session_factory() as session:
            found = await service.search(session, vehicle_type="car", query=suffix, limit=1)
        assert found.limit == 1
        assert len(found.items) == 1
        assert found.items[0].make_name == make_name

        duplicate = VehicleModel(
            id=uuid7(),
            make_id=make.id,
            name="m4",
            normalized_name="m4",
            vehicle_type="car",
        )
        with pytest.raises(IntegrityError):
            async with database.session_factory() as session, session.begin():
                session.add(duplicate)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_personal_draft_is_idempotent_private_and_optimistically_versioned() -> None:
    database = Database.create(integration_settings())
    owner = new_user()
    outsider = new_user()
    service = listing_service()
    request = ListingCreateRequest(
        owner_context=PersonalOwnerContext(type="personal"), vehicle_type="car"
    )
    key = f"phase2-personal-{uuid7()}"
    request_hash = canonical_request_hash(
        method="POST", path="/api/v1/listings", payload=request.model_dump(mode="json")
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([owner, outsider])
        async with database.session_factory() as session:
            created = await service.create(
                session,
                actor_user_id=owner.id,
                request=request,
                idempotency_key=key,
                request_hash=request_hash,
            )
        async with database.session_factory() as session:
            replay = await service.create(
                session,
                actor_user_id=owner.id,
                request=request,
                idempotency_key=key,
                request_hash=request_hash,
            )
        assert replay.id == created.id
        with pytest.raises(Exception) as hidden:
            async with database.session_factory() as session:
                await service.get(session, actor_user_id=outsider.id, listing_id=created.id)
        assert getattr(hidden.value, "code", None) == "LISTING_NOT_FOUND"

        update = ListingUpdateRequest(
            expected_version=1,
            title="Private M4 draft",
            asking_price=5_000_000,
            vehicle_spec=VehicleSpecInput(
                manufacture_year=2024,
                odometer_km=1_000,
                fuel_type="petrol",
                transmission="automatic",
                ownership_count=1,
                colour="Black",
                condition="excellent",
            ),
            car_spec=CarSpecInput(body_type="coupe", seats=4, engine_cc=2993, drivetrain="awd"),
        )
        async with database.session_factory() as session:
            updated = await service.update(
                session,
                actor_user_id=owner.id,
                listing_id=created.id,
                request=update,
            )
        assert updated.version == 2
        assert updated.vehicle_spec is not None
        assert updated.car_spec is not None
        with pytest.raises(Exception) as stale:
            async with database.session_factory() as session:
                await service.update(
                    session,
                    actor_user_id=owner.id,
                    listing_id=created.id,
                    request=update,
                )
        assert getattr(stale.value, "code", None) == "LISTING_VERSION_CONFLICT"

        async with database.session_factory() as session:
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "listing.draft.created",
                    AuditLog.resource_id == created.id,
                )
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "listing.draft.created",
                    OutboxEvent.aggregate_id == created.id,
                )
            )
        assert audit is not None
        assert event is not None

        with pytest.raises(DBAPIError):
            async with database.session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO listings "
                        "(id, owner_type, owner_user_id, owner_organization_id, "
                        "created_by_user_id, vehicle_type, lifecycle_status, currency, version) "
                        "VALUES (:id, 'user', :owner, :organization, :creator, "
                        "'car', 'draft', 'INR', 1)"
                    ),
                    {
                        "id": uuid7(),
                        "owner": owner.id,
                        "organization": uuid7(),
                        "creator": owner.id,
                    },
                )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_dealer_draft_requires_current_verified_inventory_membership() -> None:
    database = Database.create(integration_settings())
    actor = new_user()
    organization = DealerOrganization(
        id=uuid7(),
        legal_name="Phase 2 Dealer Private Limited",
        display_name="Phase 2 Dealer",
        status="active",
        verification_status="verified",
        created_by_user_id=actor.id,
    )
    membership = DealerMembership(
        id=uuid7(),
        organization_id=organization.id,
        user_id=actor.id,
        role="inventory_manager",
        status="active",
        accepted_at=datetime.now(UTC),
    )
    service = listing_service()
    request = ListingCreateRequest(
        owner_context=DealerOwnerContext(
            type="dealer_organization", organization_id=organization.id
        ),
        vehicle_type="bike",
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add(actor)
            await session.flush()
            session.add(organization)
            await session.flush()
            session.add(membership)
        key = f"phase2-dealer-{uuid7()}"
        async with database.session_factory() as session:
            created = await service.create(
                session,
                actor_user_id=actor.id,
                request=request,
                idempotency_key=key,
                request_hash=canonical_request_hash(
                    method="POST",
                    path="/api/v1/listings",
                    payload=request.model_dump(mode="json"),
                ),
            )
        assert created.owner_organization_id == organization.id
        assert created.owner_user_id is None

        async with database.session_factory() as session, session.begin():
            stored_membership = await session.get(DealerMembership, membership.id)
            assert stored_membership is not None
            stored_membership.status = "left"
            stored_membership.left_at = datetime.now(UTC)
        with pytest.raises(Exception) as denied:
            async with database.session_factory() as session:
                await service.get(session, actor_user_id=actor.id, listing_id=created.id)
        assert getattr(denied.value, "code", None) == "LISTING_NOT_FOUND"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_private_postgis_location_filters_without_projection_leakage() -> None:
    database = Database.create(integration_settings())
    owner = new_user()
    listings = listing_service()
    locations = LocationRepository()
    service = LocationService(repository=locations, listing_service=listings, audit=AuditRecorder())
    try:
        async with database.session_factory() as session, session.begin():
            session.add(owner)
        created = await create_personal_listing(database, listings, owner)
        async with database.session_factory() as session:
            projection = await service.write(
                session,
                actor_user_id=owner.id,
                listing_id=created.id,
                request=ListingLocationWriteRequest(
                    expected_version=1,
                    latitude=10.0159,
                    longitude=76.3419,
                    locality="Kakkanad",
                    coarse_area="Kochi East",
                ),
            )
        serialized = projection.model_dump(mode="json")
        assert serialized["locality"] == "Kakkanad"
        assert (
            not {
                "latitude",
                "longitude",
                "exact_point",
                "coarse_cell_hmac",
                "exact_distance",
            }
            & serialized.keys()
        )
        async with database.session_factory() as session:
            nearby = await locations.find_listing_ids_within(
                session,
                latitude=10.016,
                longitude=76.342,
                radius_meters=1_000,
                limit=10,
            )
            distant = await locations.find_listing_ids_within(
                session,
                latitude=12.9716,
                longitude=77.5946,
                radius_meters=1_000,
                limit=10,
            )
        assert created.id in nearby
        assert created.id not in distant
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_localstack_media_intent_completion_status_and_removal() -> None:
    settings = integration_settings()
    if not settings.aws_endpoint_url:
        pytest.skip("LocalStack test endpoint is not configured")
    database = Database.create(settings)
    owner = new_user()
    listings = listing_service()
    storage = MediaStorage(settings)
    media = MediaService(
        repository=MediaRepository(),
        listing_service=listings,
        storage=storage,
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )
    content = b"phase2-private-quarantine-object"
    checksum = hashlib.sha256(content).hexdigest()
    try:
        async with database.session_factory() as session, session.begin():
            session.add(owner)
        created = await create_personal_listing(database, listings, owner)
        intent_request = MediaUploadIntentRequest(
            listing_id=created.id,
            content_type="image/jpeg",
            size_bytes=len(content),
            checksum_sha256=checksum,
            sort_order=0,
        )
        async with database.session_factory() as session:
            intent = await media.create_intent(
                session,
                actor_user_id=owner.id,
                request=intent_request,
                idempotency_key=f"phase2-media-{uuid7()}",
                request_hash=canonical_request_hash(
                    method="POST",
                    path="/api/v1/media/upload-intents",
                    payload=intent_request.model_dump(mode="json"),
                ),
            )
        async with httpx.AsyncClient() as client:
            upload = await client.put(
                intent.upload_url,
                content=content,
                headers=intent.required_headers,
            )
        assert upload.status_code in {200, 204}
        async with database.session_factory() as session:
            completed = await media.complete(
                session,
                actor_user_id=owner.id,
                media_id=intent.media_id,
                request=MediaCompleteRequest(size_bytes=len(content), checksum_sha256=checksum),
            )
        assert completed.status == "processing"
        async with database.session_factory() as session:
            persisted = await media.status(
                session, actor_user_id=owner.id, media_id=intent.media_id
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "media.processing.requested",
                    OutboxEvent.aggregate_id == intent.media_id,
                )
            )
        assert persisted.status == "processing"
        assert event is not None
        assert "object_key" not in event.payload
        async with database.session_factory() as session:
            await media.remove(session, actor_user_id=owner.id, media_id=intent.media_id)
        async with database.session_factory() as session:
            removed = await media.status(session, actor_user_id=owner.id, media_id=intent.media_id)
        assert removed.status == "removed"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_owner_listing_query_is_bounded_cursor_paginated_and_isolated() -> None:
    database = Database.create(integration_settings())
    owner = new_user()
    outsider = new_user()
    service = listing_service()
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([owner, outsider])
        first = await create_personal_listing(database, service, owner)
        second = await create_personal_listing(database, service, owner, vehicle_type="bike")
        async with database.session_factory() as session:
            page_one = await service.list_owned(
                session,
                actor_user_id=owner.id,
                owner_type="personal",
                organization_id=None,
                lifecycle_status="draft",
                cursor=None,
                limit=1,
            )
        assert len(page_one.items) == 1
        assert page_one.next_cursor is not None
        async with database.session_factory() as session:
            page_two = await service.list_owned(
                session,
                actor_user_id=owner.id,
                owner_type="personal",
                organization_id=None,
                lifecycle_status="draft",
                cursor=page_one.next_cursor,
                limit=1,
            )
            outsider_page = await service.list_owned(
                session,
                actor_user_id=outsider.id,
                owner_type="personal",
                organization_id=None,
                lifecycle_status="draft",
                cursor=None,
                limit=10,
            )
        assert page_two.items
        assert {page_one.items[0].id, page_two.items[0].id} == {first.id, second.id}
        assert outsider_page.items == []
    finally:
        await database.close()
