from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Never

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import Environment, Settings
from app.core.config.settings import (
    OwnershipVerificationProviderName,
    VehicleIdentityNormalizerName,
)
from app.core.database import Database, get_session
from app.core.errors import AppError
from app.core.errors.handlers import install_exception_handlers
from app.core.idempotency import IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.core.outbox.service import enqueue_event
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.catalogue.models import CanonicalVehicle
from app.modules.dealers.models import DealerMembership, DealerOrganization
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.dependencies import (
    get_authentication_rate_limiter,
    get_current_principal,
)
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.models import Listing
from app.modules.listings.repository import ListingRepository
from app.modules.verification.models import IdentityVerification, UserVerificationState
from app.modules.verification.ownership_dependencies import get_ownership_verification_service
from app.modules.verification.ownership_models import VehicleOwnershipVerification
from app.modules.verification.ownership_provider import (
    DeterministicOwnershipVerificationProvider,
)
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.ownership_router import router as ownership_router
from app.modules.verification.ownership_schemas import OwnershipVerificationStartRequest
from app.modules.verification.ownership_service import OwnershipVerificationService
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.vehicle_identity import DeterministicVehicleIdentityNormalizer

pytestmark = pytest.mark.integration
HMAC_KEY = b"integration-vehicle-identity-hmac-key"


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
        vehicle_identity_normalizer=VehicleIdentityNormalizerName.DETERMINISTIC,
        vehicle_identity_hmac_key=SecretStr(HMAC_KEY.decode()),
        vehicle_identity_hash_version=1,
        ownership_verification_provider=OwnershipVerificationProviderName.DETERMINISTIC,
    )


def make_user(*, status: str = "active") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid7(),
        normalized_email=f"ownership-{uuid7()}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-plaintext",
        status=status,
        email_verified_at=now,
        phone_verified_at=now,
        password_changed_at=now,
    )


def build_service(*, event_writer: Any = enqueue_event) -> OwnershipVerificationService:
    return OwnershipVerificationService(
        repository=OwnershipVerificationRepository(),
        listing_repository=ListingRepository(),
        identity_repository=IdentityRepository(),
        dealer_repository=DealerRepository(),
        authorization_policy=AuthorizationPolicy(),
        identity_verification_repository=VerificationRepository(),
        provider=DeterministicOwnershipVerificationProvider(),
        normalizer=DeterministicVehicleIdentityNormalizer(),
        hmac_key=HMAC_KEY,
        hash_version=1,
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
        event_writer=event_writer,
    )


async def add_user_with_identity(database: Database, *, verified: bool = True) -> User:
    user = make_user()
    now = datetime.now(UTC)
    identity = IdentityVerification(
        id=uuid7(),
        user_id=user.id,
        attempt_number=1,
        provider_identifier="deterministic",
        provider_reference=f"identity:{uuid7()}",
        provider_result_event_id=f"identity-result:{uuid7()}" if verified else None,
        status="verified" if verified else "pending",
        assurance_level="standard" if verified else None,
        verified_at=now if verified else None,
        expires_at=now + timedelta(days=365) if verified else None,
    )
    state = UserVerificationState(
        user_id=user.id,
        current_attempt_id=identity.id,
        effective_status=identity.status,
        assurance_level=identity.assurance_level,
        verified_at=identity.verified_at,
        expires_at=identity.expires_at,
        version=1,
    )
    async with database.session_factory() as session, session.begin():
        session.add_all([user, identity, state])
    return user


async def add_listing(database: Database, user: User, *, dealer: bool = False) -> Listing:
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
        if dealer:
            organization = DealerOrganization(
                id=uuid7(),
                legal_name="Ownership Test Organization",
                display_name="Ownership Test",
                status="active",
                verification_status="verified",
                created_by_user_id=user.id,
            )
            session.add(organization)
            await session.flush()
            session.add(
                DealerMembership(
                    id=uuid7(),
                    organization_id=organization.id,
                    user_id=user.id,
                    role="owner",
                    status="active",
                    accepted_at=datetime.now(UTC),
                    version=1,
                )
            )
            listing.owner_type = "dealer_organization"
            listing.owner_user_id = None
            listing.owner_organization_id = organization.id
        session.add(listing)
    return listing


def start_request(listing: Listing, *, suffix: str) -> OwnershipVerificationStartRequest:
    return OwnershipVerificationStartRequest(
        expected_listing_version=listing.version,
        jurisdiction="IN-KL",
        registration=f"KL07{suffix}",
        chassis=f"CH{suffix}XYZ",
        ownership_basis="registered_owner",
    )


async def start_attempt(
    database: Database,
    service: OwnershipVerificationService,
    user: User,
    listing: Listing,
    request: OwnershipVerificationStartRequest,
    *,
    key: str,
) -> Any:
    async with database.session_factory() as session:
        return await service.start(
            session,
            actor_user_id=user.id,
            listing_id=listing.id,
            request=request,
            idempotency_key=key,
        )


@pytest.mark.asyncio
async def test_start_resume_concurrency_and_authorization_boundaries() -> None:
    database = Database.create(integration_settings())
    try:
        owner = await add_user_with_identity(database)
        outsider = await add_user_with_identity(database)
        unverified = await add_user_with_identity(database, verified=False)
        listing = await add_listing(database, owner)
        unverified_listing = await add_listing(database, unverified)
        dealer_listing = await add_listing(database, owner, dealer=True)
        service = build_service()
        request = start_request(listing, suffix=listing.id.hex[-8:].upper())
        key = f"ownership-resume-{uuid7()}"

        first = await start_attempt(database, service, owner, listing, request, key=key)
        replay = await start_attempt(database, service, owner, listing, request, key=key)
        assert first.attempt_id == replay.attempt_id
        assert first.capture_url is not None

        with pytest.raises(AppError, match="Current identity verification"):
            await start_attempt(
                database,
                service,
                unverified,
                unverified_listing,
                start_request(unverified_listing, suffix=unverified_listing.id.hex[-8:].upper()),
                key=f"ownership-unverified-{uuid7()}",
            )
        async with database.session_factory() as session:
            with pytest.raises(AppError, match="not found"):
                await service.status(session, actor_user_id=outsider.id, listing_id=listing.id)
        with pytest.raises(AppError, match="Dealer ownership"):
            await start_attempt(
                database,
                service,
                owner,
                dealer_listing,
                start_request(dealer_listing, suffix=dealer_listing.id.hex[-8:].upper()),
                key=f"ownership-dealer-{uuid7()}",
            )

        concurrent_owner = await add_user_with_identity(database)
        concurrent_listing = await add_listing(database, concurrent_owner)
        concurrent_request = start_request(
            concurrent_listing, suffix=concurrent_listing.id.hex[-8:].upper()
        )
        results = await asyncio.gather(
            start_attempt(
                database,
                build_service(),
                concurrent_owner,
                concurrent_listing,
                concurrent_request,
                key=f"ownership-concurrent-a-{uuid7()}",
            ),
            start_attempt(
                database,
                build_service(),
                concurrent_owner,
                concurrent_listing,
                concurrent_request,
                key=f"ownership-concurrent-b-{uuid7()}",
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in results) >= 1
        async with database.session_factory() as session:
            persisted_listing = await session.get(Listing, concurrent_listing.id)
            assert persisted_listing is not None
            canonical_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CanonicalVehicle)
                    .where(CanonicalVehicle.id == persisted_listing.canonical_vehicle_id)
                )
                or 0
            )
            unresolved_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(VehicleOwnershipVerification)
                    .where(
                        VehicleOwnershipVerification.owner_user_id == concurrent_owner.id,
                        VehicleOwnershipVerification.status.in_(
                            ("session_pending", "pending", "manual_review")
                        ),
                        VehicleOwnershipVerification.superseded_at.is_(None),
                    )
                )
                or 0
            )
        assert canonical_count == unresolved_count == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_results_are_atomic_replay_safe_and_preserve_history() -> None:
    database = Database.create(integration_settings())
    provider = DeterministicOwnershipVerificationProvider()
    try:
        owner = await add_user_with_identity(database)
        listing = await add_listing(database, owner)
        request = start_request(listing, suffix=listing.id.hex[-8:].upper())
        service = build_service()
        started = await start_attempt(
            database, service, owner, listing, request, key=f"ownership-result-{uuid7()}"
        )
        verified_at = datetime.now(UTC)
        result = provider.result(
            attempt_id=started.attempt_id,
            event_id=f"ownership-verified-{uuid7()}",
            status="verified",
            verified_at=verified_at,
            expires_at=verified_at + timedelta(days=180),
            material_attributes={"decision": "matched"},
        )
        async with database.session_factory() as session:
            applied = await service.apply_provider_result(session, result)
        async with database.session_factory() as session:
            duplicate = await service.apply_provider_result(session, result)
        assert applied.disposition == "applied"
        assert duplicate.disposition == "duplicate"
        with pytest.raises(AppError, match="conflicts"):
            async with database.session_factory() as session:
                await service.apply_provider_result(
                    session,
                    provider.result(
                        attempt_id=started.attempt_id,
                        event_id=f"ownership-conflict-{uuid7()}",
                        status="failed",
                        safe_failure_code="OWNERSHIP_MISMATCH",
                    ),
                )

        persisted_listing_version = started.listing_version
        later_request = request.model_copy(
            update={"expected_listing_version": persisted_listing_version}
        )
        later = await start_attempt(
            database,
            service,
            owner,
            listing,
            later_request,
            key=f"ownership-later-{uuid7()}",
        )
        async with database.session_factory() as session:
            failed = await service.apply_provider_result(
                session,
                provider.result(
                    attempt_id=later.attempt_id,
                    event_id=f"ownership-failed-{uuid7()}",
                    status="failed",
                    safe_failure_code="provider-private-detail",
                ),
            )
        assert failed.status == "failed"

        rollback_attempt = await start_attempt(
            database,
            service,
            owner,
            listing,
            later_request,
            key=f"ownership-rollback-{uuid7()}",
        )

        def fail_event(*args: Any, **kwargs: Any) -> Never:
            del args, kwargs
            raise RuntimeError("forced outbox failure")

        failing_service = build_service(event_writer=fail_event)
        rollback_result = provider.result(
            attempt_id=rollback_attempt.attempt_id,
            event_id=f"ownership-rollback-result-{uuid7()}",
            status="manual_review",
        )
        with pytest.raises(RuntimeError, match="forced outbox"):
            async with database.session_factory() as session:
                await failing_service.apply_provider_result(session, rollback_result)

        async with database.session_factory() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(VehicleOwnershipVerification)
                        .where(VehicleOwnershipVerification.owner_user_id == owner.id)
                        .order_by(VehicleOwnershipVerification.attempt_number)
                    )
                ).all()
            )
            verified_audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.resource_id == started.attempt_id,
                    AuditLog.changes["status"].astext == "verified",
                )
            )
            verified_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.payload["attempt_id"].astext == str(started.attempt_id),
                    OutboxEvent.payload["status"].astext == "verified",
                )
            )
            rolled_back = await session.get(
                VehicleOwnershipVerification, rollback_attempt.attempt_id
            )
        assert [attempt.status for attempt in attempts] == ["verified", "failed", "pending"]
        assert verified_audit is not None and verified_event is not None
        assert set(verified_event.payload) == {
            "owner_user_id",
            "listing_id",
            "canonical_vehicle_id",
            "attempt_id",
            "identity_projection_version",
            "vehicle_identity_version",
            "status",
            "ownership_basis",
            "failure_code",
        }
        assert rolled_back is not None and rolled_back.provider_result_event_id is None
        assert rolled_back.status == "pending"

        async with database.session_factory() as session:
            reviewed = await service.apply_provider_result(session, rollback_result)
        async with database.session_factory() as session:
            reviewed_attempt = await session.get(
                VehicleOwnershipVerification, rollback_attempt.attempt_id
            )
        assert reviewed.status == "manual_review"
        assert reviewed_attempt is not None
        assert reviewed_attempt.safe_failure_code == "MANUAL_REVIEW_REQUIRED"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_stale_identity_result_cannot_become_effective() -> None:
    database = Database.create(integration_settings())
    provider = DeterministicOwnershipVerificationProvider()
    try:
        owner = await add_user_with_identity(database)
        listing = await add_listing(database, owner)
        service = build_service()
        started = await start_attempt(
            database,
            service,
            owner,
            listing,
            start_request(listing, suffix=listing.id.hex[-8:].upper()),
            key=f"ownership-stale-{uuid7()}",
        )
        async with database.session_factory() as session, session.begin():
            state = await session.get(UserVerificationState, owner.id, with_for_update=True)
            assert state is not None
            state.version += 1
        verified_at = datetime.now(UTC)
        async with database.session_factory() as session:
            response = await service.apply_provider_result(
                session,
                provider.result(
                    attempt_id=started.attempt_id,
                    event_id=f"ownership-stale-result-{uuid7()}",
                    status="verified",
                    verified_at=verified_at,
                    expires_at=verified_at + timedelta(days=180),
                ),
            )
        async with database.session_factory() as session:
            attempt = await session.get(VehicleOwnershipVerification, started.attempt_id)
        assert response.disposition == "stale"
        assert attempt is not None and attempt.status == "pending"
        assert attempt.provider_result_event_id is None and attempt.superseded_at is not None
    finally:
        await database.close()


class AllowLimiter:
    async def enforce(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


@pytest.mark.asyncio
async def test_api_is_privacy_safe_and_cross_owner_is_denied() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    try:
        owner = await add_user_with_identity(database)
        outsider = await add_user_with_identity(database)
        listing = await add_listing(database, owner)
        service = build_service()
        app = FastAPI()
        app.state.settings = settings
        install_exception_handlers(app)
        app.include_router(ownership_router)

        async def session_override() -> Any:
            async with database.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_ownership_verification_service] = lambda: service
        app.dependency_overrides[get_authentication_rate_limiter] = lambda: AllowLimiter()
        app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(user_id=owner.id)
        registration = f"KL07{listing.id.hex[-8:].upper()}"
        chassis = f"CH{listing.id.hex[-8:].upper()}XYZ"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            started = await client.post(
                f"/api/v1/listings/{listing.id}/ownership-verification/start",
                headers={"Idempotency-Key": f"ownership-api-{uuid7()}"},
                json={
                    "expected_listing_version": 1,
                    "jurisdiction": "IN-KL",
                    "registration": registration,
                    "chassis": chassis,
                    "ownership_basis": "registered_owner",
                },
            )
            status_response = await client.get(
                f"/api/v1/listings/{listing.id}/ownership-verification/status"
            )
            app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(
                user_id=outsider.id
            )
            outsider_response = await client.get(
                f"/api/v1/listings/{listing.id}/ownership-verification/status"
            )
        assert started.status_code == 201
        assert started.json()["capture_url"].startswith("https://ownership.local.test/")
        assert status_response.status_code == 200
        body = status_response.json()
        assert set(body) == {
            "attempt_id",
            "canonical_vehicle_id",
            "status",
            "ownership_basis",
            "verified_at",
            "expires_at",
            "revoked_at",
            "failure_code",
            "updated_at",
        }
        assert outsider_response.status_code == 404
        assert {
            "provider_reference",
            "provider_result_event_id",
            "material_fingerprint",
            "registration_hmac",
            "vin_hmac",
            "chassis_hmac",
            "capture_url",
            "documents",
            "evidence",
        }.isdisjoint(body)

        async with database.session_factory() as session:
            attempt = await session.get(VehicleOwnershipVerification, started.json()["attempt_id"])
            canonical = await session.get(CanonicalVehicle, started.json()["canonical_vehicle_id"])
            assert attempt is not None and canonical is not None
            audits = list(
                (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.resource_id == attempt.id)
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.payload["attempt_id"].astext == str(attempt.id)
                        )
                    )
                ).all()
            )
        persisted = json.dumps(
            {
                "attempt": {
                    column.name: getattr(attempt, column.name)
                    for column in VehicleOwnershipVerification.__table__.columns
                },
                "canonical": {
                    column.name: getattr(canonical, column.name)
                    for column in CanonicalVehicle.__table__.columns
                },
                "audits": [row.changes for row in audits],
                "events": [row.payload for row in events],
            },
            default=str,
        )
        assert registration not in persisted
        assert chassis not in persisted
        assert "ownership.local.test" not in persisted
    finally:
        await database.close()
