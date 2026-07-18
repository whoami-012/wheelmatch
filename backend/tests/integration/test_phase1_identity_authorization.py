from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Environment, Settings
from app.core.database import Database
from app.core.errors import AppError
from app.core.idempotency import IdempotencyRepository, canonical_request_hash
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.core.security import AccessTokenService, PasswordService, SecretHasher
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.authorization.cache import NullAuthorizationCache, RedisAuthorizationCache
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.authorization.service import CapabilityService
from app.modules.dealers.delivery import NullDealerInvitationDelivery
from app.modules.dealers.models import DealerMembership, DealerOrganization
from app.modules.dealers.repository import DealerRepository
from app.modules.dealers.schemas import MembershipUpdateRequest, OrganizationCreateRequest
from app.modules.dealers.service import DealerService
from app.modules.identity.models import SessionFamily, User, VerificationChallenge
from app.modules.identity.rate_limit import AuthenticationRateLimiter, RateLimitRule
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.schemas import LoginRequest, RegisterRequest, TokenResponse
from app.modules.identity.service import IdentityService
from app.modules.identity.session_service import SessionService
from app.modules.profiles.models import Profile, SellerProfile
from app.modules.profiles.repository import ProfileRepository
from app.modules.profiles.schemas import ProfileUpdateRequest
from app.modules.profiles.service import ProfileService

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
    )


def new_user(*, email: str, phone: str | None = None) -> User:
    return User(
        id=uuid7(),
        normalized_email=email,
        normalized_phone=phone,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-a-plaintext-password-hash",
        status="active",
        password_changed_at=datetime.now(UTC),
    )


def build_session_service(passwords: PasswordService) -> SessionService:
    return SessionService(
        repository=IdentityRepository(),
        password_service=passwords,
        secret_hasher=SecretHasher("test-secret-hash-key-for-integration"),
        access_tokens=AccessTokenService(
            signing_key="test-access-signing-key-for-integration",
            issuer="wheelmatch-integration",
            audience="wheelmatch-integration-client",
            ttl_seconds=900,
        ),
        audit=AuditRecorder(),
        authorization_cache=NullAuthorizationCache(),
        refresh_ttl_seconds=86400,
        login_failure_threshold=5,
        login_lock_seconds=900,
    )


class CaptureIdentityDelivery:
    def __init__(self) -> None:
        self.verifications: dict[tuple[str, UUID], str] = {}
        self.recoveries: dict[UUID, str] = {}

    async def send_verification(
        self,
        *,
        user_id: UUID,
        kind: str,
        destination: str,
        challenge_id: UUID,
        code: str,
    ) -> None:
        del user_id, destination
        self.verifications[(kind, challenge_id)] = code

    async def send_recovery(
        self,
        *,
        user_id: UUID,
        destination: str,
        challenge_id: UUID,
        token: str,
    ) -> None:
        del user_id, destination
        self.recoveries[challenge_id] = token


class CaptureAuthorizationCache(NullAuthorizationCache):
    def __init__(self) -> None:
        self.invalidated: list[UUID] = []

    async def invalidate(self, user_ids: Iterable[UUID]) -> None:
        self.invalidated.extend(user_ids)


class CaptureDealerInvitationDelivery:
    def __init__(self) -> None:
        self.tokens: dict[UUID, str] = {}

    async def send_invitation(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        membership_id: UUID,
        token: str,
    ) -> None:
        del user_id, organization_id
        self.tokens[membership_id] = token


class FailingRedis:
    async def eval(self, *args: object) -> object:
        del args
        raise RuntimeError("simulated Redis unavailability")


def build_identity_service(
    passwords: PasswordService, delivery: CaptureIdentityDelivery
) -> IdentityService:
    return IdentityService(
        repository=IdentityRepository(),
        password_service=passwords,
        secret_hasher=SecretHasher("test-secret-hash-key-for-integration"),
        audit=AuditRecorder(),
        delivery=delivery,
        authorization_cache=NullAuthorizationCache(),
        idempotency_repository=IdempotencyRepository(),
        verification_ttl_seconds=900,
        recovery_ttl_seconds=1800,
    )


@pytest.mark.asyncio
async def test_phase1_migration_tables_constraints_and_indexes_exist() -> None:
    database = Database.create(integration_settings())
    expected_tables = {
        "users",
        "profiles",
        "seller_profiles",
        "verification_challenges",
        "password_recovery_challenges",
        "session_families",
        "refresh_sessions",
        "rate_limit_buckets",
        "dealer_organizations",
        "dealer_memberships",
        "audit_logs",
    }
    expected_indexes = {
        "uq_users_normalized_email_active",
        "uq_users_normalized_phone_active",
        "ix_session_families_user_active",
        "ix_refresh_sessions_active_expiry",
        "uq_dealer_membership_org_user",
    }
    try:
        async with database.engine.connect() as connection:
            tables = set(
                (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
            indexes = set(
                (
                    await connection.execute(
                        text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
            constraint_names = set(
                (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE connamespace = 'public'::regnamespace"
                        )
                    )
                ).scalars()
            )
        assert expected_tables <= tables
        assert expected_indexes - {"uq_dealer_membership_org_user"} <= indexes
        assert "uq_dealer_membership_org_user" in constraint_names
        assert "ck_dealer_memberships_acceptance_state_valid" in constraint_names
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_user_email_and_phone_normalization_uniqueness_is_database_enforced() -> None:
    database = Database.create(integration_settings())
    email = f"phase1-{uuid7()}@example.test"
    phone = f"+19{str(uuid7().int)[-13:]}"
    first = new_user(email=email, phone=phone)
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([first, Profile(user_id=first.id)])
        with pytest.raises(IntegrityError):
            async with database.session_factory() as session, session.begin():
                session.add(new_user(email=email))
                await session.flush()
        with pytest.raises(IntegrityError):
            async with database.session_factory() as session, session.begin():
                session.add(new_user(email=f"other-{uuid7()}@example.test", phone=phone))
                await session.flush()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_dealer_membership_uniqueness_and_lifecycle_constraints_are_enforced() -> None:
    database = Database.create(integration_settings())
    owner = new_user(email=f"owner-{uuid7()}@example.test")
    member = new_user(email=f"member-{uuid7()}@example.test")
    organization = DealerOrganization(
        id=uuid7(),
        legal_name="Phase 1 Dealer Private Limited",
        display_name="Phase 1 Dealer",
        status="active",
        verification_status="verified",
        created_by_user_id=owner.id,
    )
    now = datetime.now(UTC)
    membership = DealerMembership(
        id=uuid7(),
        organization_id=organization.id,
        user_id=member.id,
        role="sales_agent",
        status="invited",
        invited_by_user_id=owner.id,
        invitation_token_hash=str(uuid7()).replace("-", "") * 2,
        invite_expires_at=now + timedelta(days=7),
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([owner, member])
            await session.flush()
            session.add(organization)
            await session.flush()
            session.add(membership)
        with pytest.raises(IntegrityError):
            async with database.session_factory() as session, session.begin():
                session.add(
                    DealerMembership(
                        id=uuid7(),
                        organization_id=organization.id,
                        user_id=member.id,
                        role="admin",
                        status="active",
                        accepted_at=now,
                    )
                )
                await session.flush()
        with pytest.raises(IntegrityError):
            async with database.session_factory() as session, session.begin():
                invalid = DealerMembership(
                    id=uuid7(),
                    organization_id=organization.id,
                    user_id=owner.id,
                    role="not_a_role",
                    status="active",
                    accepted_at=now,
                )
                session.add(invalid)
                await session.flush()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_organization_creation_commits_owner_membership_audit_and_outbox() -> None:
    database = Database.create(integration_settings())
    cache = CaptureAuthorizationCache()
    service = DealerService(
        repository=DealerRepository(),
        identity_repository=IdentityRepository(),
        policy=AuthorizationPolicy(),
        secret_hasher=SecretHasher("test-secret-hash-key-for-integration"),
        audit=AuditRecorder(),
        delivery=NullDealerInvitationDelivery(),
        authorization_cache=cache,
        idempotency_repository=IdempotencyRepository(),
        invitation_ttl_seconds=86400,
    )
    owner = new_user(email=f"organization-owner-{uuid7()}@example.test")
    request = OrganizationCreateRequest(
        legal_name="Transaction Dealer Private Limited",
        display_name="Transaction Dealer",
    )
    idempotency_key = f"organization-{uuid7()}"
    request_hash = canonical_request_hash(
        method="POST",
        path="/api/v1/dealer-organizations",
        payload=request.model_dump(mode="json"),
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([owner, Profile(user_id=owner.id)])

        async with database.session_factory() as session:
            created = await service.create_organization(
                session,
                actor_user_id=owner.id,
                request=request,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )

        async with database.session_factory() as session:
            membership = await session.scalar(
                select(DealerMembership).where(
                    DealerMembership.organization_id == created.id,
                    DealerMembership.user_id == owner.id,
                )
            )
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "dealer.organization.created",
                    AuditLog.resource_id == created.id,
                )
            )
            outbox = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "dealer.organization.created",
                    OutboxEvent.aggregate_id == created.id,
                )
            )
        assert membership is not None
        assert membership.role == "owner"
        assert membership.status == "active"
        assert audit is not None
        assert audit.membership_id == membership.id
        assert outbox is not None
        assert owner.id in cache.invalidated
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_login_refresh_rotation_and_concurrent_replay_revoke_the_family() -> None:
    database = Database.create(integration_settings())
    passwords = PasswordService()
    service = build_session_service(passwords)
    password = f"Valid-{uuid7()}-password"
    user = new_user(email=f"session-{uuid7()}@example.test")
    user.password_hash = passwords.hash(password, normalized_email=user.normalized_email)
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([user, Profile(user_id=user.id)])

        async with database.session_factory() as session:
            tokens = await service.login(
                session,
                LoginRequest(
                    email=user.normalized_email,
                    password=SecretStr(password),
                    device_name="Integration device",
                    device_platform="android",
                ),
            )

        async def rotate() -> TokenResponse:
            async with database.session_factory() as session:
                return await service.refresh(session, refresh_token=tokens.refresh_token)

        results = await asyncio.gather(rotate(), rotate(), return_exceptions=True)
        successes = [result for result in results if isinstance(result, TokenResponse)]
        failures = [result for result in results if isinstance(result, AppError)]

        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == "SESSION_INVALID"
        async with database.session_factory() as session:
            family = await session.scalar(
                select(SessionFamily).where(SessionFamily.id == tokens.session_id)
            )
            assert family is not None
            assert family.revoked_at is not None
            assert family.reuse_detected_at is not None
            with pytest.raises(AppError, match="Authentication is required"):
                await service.authenticate_access_token(
                    session,
                    access_token=successes[0].access_token,
                )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_password_change_revokes_sessions_and_login_failures_remain_generic() -> None:
    database = Database.create(integration_settings())
    passwords = PasswordService()
    service = build_session_service(passwords)
    old_password = f"Old-{uuid7()}-password"
    new_password = f"New-{uuid7()}-password"
    user = new_user(email=f"password-{uuid7()}@example.test")
    user.password_hash = passwords.hash(old_password, normalized_email=user.normalized_email)
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([user, Profile(user_id=user.id)])
        async with database.session_factory() as session:
            tokens = await service.login(
                session,
                LoginRequest(
                    email=user.normalized_email,
                    password=SecretStr(old_password),
                    device_platform="android",
                ),
            )
            principal = await service.authenticate_access_token(
                session, access_token=tokens.access_token
            )
            await service.change_password(
                session,
                principal=principal,
                current_password=old_password,
                new_password=new_password,
            )

        async with database.session_factory() as session:
            with pytest.raises(AppError) as revoked:
                await service.authenticate_access_token(session, access_token=tokens.access_token)
            assert revoked.value.code == "SESSION_INVALID"

        failures: list[str] = []
        for email, password in (
            (user.normalized_email, old_password),
            (f"missing-{uuid7()}@example.test", old_password),
        ):
            async with database.session_factory() as session:
                with pytest.raises(AppError) as failure:
                    await service.login(
                        session,
                        LoginRequest(
                            email=email,
                            password=SecretStr(password),
                            device_platform="unknown",
                        ),
                    )
                failures.append(failure.value.code)
        assert failures == ["AUTHENTICATION_FAILED", "AUTHENTICATION_FAILED"]

        async with database.session_factory() as session:
            replacement = await service.login(
                session,
                LoginRequest(
                    email=user.normalized_email,
                    password=SecretStr(new_password),
                    device_platform="android",
                ),
            )
            assert replacement.refresh_token != tokens.refresh_token
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_registration_verification_recovery_and_idempotent_retry() -> None:
    database = Database.create(integration_settings())
    passwords = PasswordService()
    delivery = CaptureIdentityDelivery()
    identity = build_identity_service(passwords, delivery)
    sessions = build_session_service(passwords)
    password = f"Register-{uuid7()}-password"
    replacement_password = f"Recovered-{uuid7()}-password"
    request = RegisterRequest(
        email=f"register-{uuid7()}@example.test",
        phone=f"+19{str(uuid7().int)[-13:]}",
        password=SecretStr(password),
        display_name="Phase One User",
    )
    idempotency_key = f"registration-{uuid7()}"
    request_hash = canonical_request_hash(
        method="POST",
        path="/api/v1/auth/register",
        payload={
            "email": request.email,
            "phone": request.phone,
            "password": password,
            "display_name": request.display_name,
        },
    )
    try:
        async with database.session_factory() as session:
            created = await identity.register(
                session,
                request,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        delivery_count = len(delivery.verifications)
        async with database.session_factory() as session:
            replay = await identity.register(
                session,
                request,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        assert replay == created
        assert len(delivery.verifications) == delivery_count

        email_challenge = next(
            item for item in created.verification_challenges if item.kind == "email"
        )
        email_code = delivery.verifications[("email", email_challenge.challenge_id)]
        async with database.session_factory() as session:
            with pytest.raises(AppError) as wrong_code:
                await identity.verify_challenge(
                    session,
                    challenge_id=email_challenge.challenge_id,
                    code="000000" if email_code != "000000" else "999999",
                    expected_kind="email",
                )
            assert wrong_code.value.code == "VERIFICATION_FAILED"
        async with database.session_factory() as session:
            verified = await identity.verify_challenge(
                session,
                challenge_id=email_challenge.challenge_id,
                code=email_code,
                expected_kind="email",
            )
            assert verified.verified
        async with database.session_factory() as session:
            with pytest.raises(AppError) as single_use:
                await identity.verify_challenge(
                    session,
                    challenge_id=email_challenge.challenge_id,
                    code=email_code,
                    expected_kind="email",
                )
            assert single_use.value.code == "VERIFICATION_FAILED"

        async with database.session_factory() as session:
            tokens = await sessions.login(
                session,
                LoginRequest(
                    email=request.email,
                    password=SecretStr(password),
                    device_platform="android",
                ),
            )
        async with database.session_factory() as session:
            accepted = await identity.request_recovery(session, email=request.email)
            assert accepted.accepted
        recovery_token = next(iter(delivery.recoveries.values()))
        async with database.session_factory() as session:
            await identity.reset_password(
                session,
                token=recovery_token,
                new_password=replacement_password,
            )
        async with database.session_factory() as session:
            with pytest.raises(AppError) as consumed:
                await identity.reset_password(
                    session,
                    token=recovery_token,
                    new_password=f"Another-{uuid7()}-password",
                )
            assert consumed.value.code == "RECOVERY_FAILED"
        async with database.session_factory() as session:
            with pytest.raises(AppError) as revoked:
                await sessions.authenticate_access_token(session, access_token=tokens.access_token)
            assert revoked.value.code == "SESSION_INVALID"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_verification_attempt_limit_and_expiry_are_enforced() -> None:
    database = Database.create(integration_settings())
    passwords = PasswordService()
    delivery = CaptureIdentityDelivery()
    identity = build_identity_service(passwords, delivery)
    secrets = SecretHasher("test-secret-hash-key-for-integration")
    user = new_user(email=f"verification-{uuid7()}@example.test")
    valid_code = "123456"
    limited = VerificationChallenge(
        id=uuid7(),
        user_id=user.id,
        kind="email",
        secret_hash=secrets.digest(valid_code),
        max_attempts=2,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    expired = VerificationChallenge(
        id=uuid7(),
        user_id=user.id,
        kind="phone",
        secret_hash=secrets.digest(valid_code),
        max_attempts=5,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([user, Profile(user_id=user.id), limited, expired])

        for code in ("000000", "999999", valid_code):
            async with database.session_factory() as session:
                with pytest.raises(AppError) as failure:
                    await identity.verify_challenge(
                        session,
                        challenge_id=limited.id,
                        code=code,
                        expected_kind="email",
                    )
                assert failure.value.code == "VERIFICATION_FAILED"

        async with database.session_factory() as session:
            stored = await session.get(VerificationChallenge, limited.id)
            assert stored is not None
            assert stored.attempt_count == stored.max_attempts == 2
        async with database.session_factory() as session:
            with pytest.raises(AppError) as expiry:
                await identity.verify_challenge(
                    session,
                    challenge_id=expired.id,
                    code=valid_code,
                    expected_kind="phone",
                )
            assert expiry.value.code == "VERIFICATION_FAILED"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_session_listing_object_scope_logout_and_logout_all() -> None:
    database = Database.create(integration_settings())
    passwords = PasswordService()
    service = build_session_service(passwords)
    password = f"Sessions-{uuid7()}-password"
    user = new_user(email=f"sessions-{uuid7()}@example.test")
    user.password_hash = passwords.hash(password, normalized_email=user.normalized_email)
    outsider = new_user(email=f"outsider-{uuid7()}@example.test")
    outsider_password = f"Outsider-{uuid7()}-password"
    outsider.password_hash = passwords.hash(
        outsider_password, normalized_email=outsider.normalized_email
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all(
                [user, Profile(user_id=user.id), outsider, Profile(user_id=outsider.id)]
            )

        async def log_in(email: str, candidate_password: str, device_name: str) -> TokenResponse:
            async with database.session_factory() as session:
                return await service.login(
                    session,
                    LoginRequest(
                        email=email,
                        password=SecretStr(candidate_password),
                        device_name=device_name,
                        device_platform="android",
                    ),
                )

        first = await log_in(user.normalized_email, password, "First")
        second = await log_in(user.normalized_email, password, "Second")
        outsider_tokens = await log_in(outsider.normalized_email, outsider_password, "Outsider")
        async with database.session_factory() as session:
            principal = await service.authenticate_access_token(
                session, access_token=first.access_token
            )
            listed = await service.list_sessions(session, principal=principal)
            assert {item.id for item in listed} == {first.session_id, second.session_id}
            await session.rollback()
            with pytest.raises(AppError) as object_scope:
                await service.revoke_session(
                    session,
                    principal=principal,
                    family_id=outsider_tokens.session_id,
                )
            assert object_scope.value.code == "SESSION_NOT_FOUND"
            await service.revoke_session(
                session,
                principal=principal,
                family_id=second.session_id,
            )

        async with database.session_factory() as session:
            with pytest.raises(AppError):
                await service.authenticate_access_token(session, access_token=second.access_token)
            await service.authenticate_access_token(session, access_token=first.access_token)

        third = await log_in(user.normalized_email, password, "Third")
        async with database.session_factory() as session:
            principal = await service.authenticate_access_token(
                session, access_token=first.access_token
            )
            assert await service.logout_all(session, principal=principal) == 2

        for access_token in (first.access_token, third.access_token):
            async with database.session_factory() as session:
                with pytest.raises(AppError) as revoked:
                    await service.authenticate_access_token(session, access_token=access_token)
                assert revoked.value.code == "SESSION_INVALID"

        async with database.session_factory() as session:
            actions = set((await session.scalars(select(AuditLog.action))).all())
            assert "identity.sessions.revoked_all" in actions
            assert "identity.session.revoked" in actions
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_membership_transition_is_serialized_and_invalidates_cache() -> None:
    database = Database.create(integration_settings())
    cache = CaptureAuthorizationCache()
    service = DealerService(
        repository=DealerRepository(),
        identity_repository=IdentityRepository(),
        policy=AuthorizationPolicy(),
        secret_hasher=SecretHasher("test-secret-hash-key-for-integration"),
        audit=AuditRecorder(),
        delivery=NullDealerInvitationDelivery(),
        authorization_cache=cache,
        idempotency_repository=IdempotencyRepository(),
        invitation_ttl_seconds=86400,
    )
    owner = new_user(email=f"transition-owner-{uuid7()}@example.test")
    member = new_user(email=f"transition-member-{uuid7()}@example.test")
    organization = DealerOrganization(
        id=uuid7(),
        legal_name="Concurrent Dealer Private Limited",
        display_name="Concurrent Dealer",
        status="active",
        verification_status="verified",
        created_by_user_id=owner.id,
    )
    owner_membership = DealerMembership(
        id=uuid7(),
        organization_id=organization.id,
        user_id=owner.id,
        role="owner",
        status="active",
        accepted_at=datetime.now(UTC),
    )
    target_membership = DealerMembership(
        id=uuid7(),
        organization_id=organization.id,
        user_id=member.id,
        role="sales_agent",
        status="active",
        accepted_at=datetime.now(UTC),
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([owner, member])
            await session.flush()
            session.add(organization)
            await session.flush()
            session.add_all([owner_membership, target_membership])

        async def transition(status: Literal["suspended", "revoked"]) -> object:
            async with database.session_factory() as session:
                return await service.update_membership(
                    session,
                    actor_user_id=owner.id,
                    organization_id=organization.id,
                    membership_id=target_membership.id,
                    request=MembershipUpdateRequest(
                        status=status,
                        expected_version=1,
                    ),
                )

        results = await asyncio.gather(
            transition("suspended"), transition("revoked"), return_exceptions=True
        )
        successes = [result for result in results if not isinstance(result, BaseException)]
        conflicts = [result for result in results if isinstance(result, AppError)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert conflicts[0].code == "MEMBERSHIP_VERSION_CONFLICT"

        async with database.session_factory() as session:
            stored = await session.get(DealerMembership, target_membership.id)
            assert stored is not None
            assert stored.version == 2
            assert stored.status in {"suspended", "revoked"}
        assert owner.id in cache.invalidated
        assert member.id in cache.invalidated
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_profile_patch_is_current_user_scoped_and_preserves_omitted_fields() -> None:
    database = Database.create(integration_settings())
    service = ProfileService(
        repository=ProfileRepository(),
        identity_repository=IdentityRepository(),
        audit=AuditRecorder(),
        authorization_cache=NullAuthorizationCache(),
    )
    actor = new_user(email=f"profile-{uuid7()}@example.test")
    original = Profile(
        user_id=actor.id,
        display_name="Original Name",
        home_locality="Original Locality",
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([actor, original])
        async with database.session_factory() as session:
            updated = await service.update_profile(
                session,
                actor_user_id=actor.id,
                request=ProfileUpdateRequest(
                    display_name="Updated Name",
                    expected_version=1,
                ),
            )
        assert updated.user_id == actor.id
        assert updated.display_name == "Updated Name"
        assert updated.home_locality == "Original Locality"
        assert updated.version == 2
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_capabilities_are_projected_cached_and_invalidated_in_real_redis() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    cache = RedisAuthorizationCache(redis, ttl_seconds=300)
    service = CapabilityService(
        identity_repository=IdentityRepository(),
        profile_repository=ProfileRepository(),
        dealer_repository=DealerRepository(),
        policy=AuthorizationPolicy(),
        cache=cache,
    )
    now = datetime.now(UTC)
    actor = new_user(email=f"capabilities-{uuid7()}@example.test")
    actor.email_verified_at = now
    actor.phone_verified_at = now
    seller = SellerProfile(
        user_id=actor.id,
        status="active",
        readiness_state="ready",
        activated_at=now,
    )
    organization = DealerOrganization(
        id=uuid7(),
        legal_name="Capability Dealer Private Limited",
        display_name="Capability Dealer",
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
        accepted_at=now,
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add(actor)
            await session.flush()
            session.add_all([Profile(user_id=actor.id), seller, organization])
            await session.flush()
            session.add(membership)
        version = actor.authorization_version

        assert await cache.get(actor.id, expected_version=version) is None
        async with database.session_factory() as session:
            projected = await service.get_capabilities(session, actor_user_id=actor.id)
        assert projected.buyer
        assert projected.personal_seller
        assert projected.can_create_private_draft
        assert len(projected.dealer) == 1
        assert projected.dealer[0].permissions == ["organization.inventory.manage"]

        cached = await cache.get(actor.id, expected_version=version)
        assert cached == projected.model_dump(mode="json")
        assert await cache.get(actor.id, expected_version=version + 1) is None
        async with database.session_factory() as session:
            cache_hit = await service.get_capabilities(session, actor_user_id=actor.id)
        assert cache_hit == projected

        await cache.invalidate([actor.id, actor.id])
        assert await cache.get(actor.id, expected_version=version) is None
    finally:
        await cache.invalidate([actor.id])
        await redis.aclose()
        await database.close()


@pytest.mark.asyncio
async def test_authentication_rate_limit_uses_redis_and_postgresql_fallback() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    secrets = SecretHasher("test-rate-limit-secret-for-integration")
    redis_subject = f"redis-subject-{uuid7()}"
    redis_rule = RateLimitRule(scope=f"integration.redis.{uuid7()}", limit=1, window_seconds=60)
    redis_limiter = AuthenticationRateLimiter(redis, secret_hasher=secrets)
    fallback_subject = f"fallback-subject-{uuid7()}"
    fallback_rule = RateLimitRule(
        scope=f"integration.postgresql.{uuid7()}", limit=1, window_seconds=60
    )
    fallback_limiter = AuthenticationRateLimiter(cast(Redis, FailingRedis()), secret_hasher=secrets)
    try:
        async with database.session_factory() as session:
            await redis_limiter.enforce(
                session,
                rule=redis_rule,
                subjects=[redis_subject, redis_subject],
            )
            with pytest.raises(AppError) as redis_limited:
                await redis_limiter.enforce(
                    session,
                    rule=redis_rule,
                    subjects=[redis_subject],
                )
        assert redis_limited.value.code == "AUTH_RATE_LIMITED"

        async with database.session_factory() as session:
            await fallback_limiter.enforce(
                session,
                rule=fallback_rule,
                subjects=[fallback_subject, fallback_subject],
            )
        async with database.session_factory() as session:
            with pytest.raises(AppError) as fallback_limited:
                await fallback_limiter.enforce(
                    session,
                    rule=fallback_rule,
                    subjects=[fallback_subject],
                )
        assert fallback_limited.value.code == "AUTH_RATE_LIMITED"
    finally:
        redis_key = f"rate:{redis_rule.scope}:{secrets.digest(redis_subject)}"
        await redis.delete(redis_key)
        await redis.aclose()
        await database.close()


@pytest.mark.asyncio
async def test_seller_profile_readiness_creation_is_persistent_and_idempotent() -> None:
    database = Database.create(integration_settings())
    cache = CaptureAuthorizationCache()
    service = ProfileService(
        repository=ProfileRepository(),
        identity_repository=IdentityRepository(),
        audit=AuditRecorder(),
        authorization_cache=cache,
    )
    actor = new_user(email=f"seller-profile-{uuid7()}@example.test")
    try:
        async with database.session_factory() as session, session.begin():
            session.add(actor)
            await session.flush()
            session.add(Profile(user_id=actor.id))

        async with database.session_factory() as session:
            initial = await service.get_seller_readiness(session, actor_user_id=actor.id)
        assert initial.status == "pending"
        assert initial.readiness_state == "not_ready"
        assert set(initial.missing_requirements) == {
            "email_verification",
            "phone_verification",
            "identity_verification",
            "publication_policy",
        }

        async with database.session_factory() as session:
            created = await service.create_seller_profile(session, actor_user_id=actor.id)
        async with database.session_factory() as session:
            replay = await service.create_seller_profile(session, actor_user_id=actor.id)
        assert replay == created
        assert actor.id in cache.invalidated

        async with database.session_factory() as session:
            stored = await session.get(SellerProfile, actor.id)
            stored_user = await session.get(User, actor.id)
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "profile.seller.created",
                    AuditLog.resource_id == actor.id,
                )
            )
            outbox = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "profile.seller.created",
                    OutboxEvent.aggregate_id == actor.id,
                )
            )
        assert stored is not None
        assert stored.status == "pending"
        assert stored.readiness_state == "not_ready"
        assert stored_user is not None
        assert stored_user.authorization_version == 2
        assert audit is not None
        assert outbox is not None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_dealer_invitation_acceptance_and_leave_lifecycle() -> None:
    database = Database.create(integration_settings())
    cache = CaptureAuthorizationCache()
    delivery = CaptureDealerInvitationDelivery()
    service = DealerService(
        repository=DealerRepository(),
        identity_repository=IdentityRepository(),
        policy=AuthorizationPolicy(),
        secret_hasher=SecretHasher("test-secret-hash-key-for-integration"),
        audit=AuditRecorder(),
        delivery=delivery,
        authorization_cache=cache,
        idempotency_repository=IdempotencyRepository(),
        invitation_ttl_seconds=86400,
    )
    owner = new_user(email=f"lifecycle-owner-{uuid7()}@example.test")
    member = new_user(email=f"lifecycle-member-{uuid7()}@example.test")
    organization = DealerOrganization(
        id=uuid7(),
        legal_name="Lifecycle Dealer Private Limited",
        display_name="Lifecycle Dealer",
        status="active",
        verification_status="verified",
        created_by_user_id=owner.id,
    )
    owner_membership = DealerMembership(
        id=uuid7(),
        organization_id=organization.id,
        user_id=owner.id,
        role="owner",
        status="active",
        accepted_at=datetime.now(UTC),
    )
    idempotency_key = f"membership-invite-{uuid7()}"
    request_hash = canonical_request_hash(
        method="POST",
        path=f"/api/v1/dealer-organizations/{organization.id}/memberships",
        payload={"user_id": str(member.id), "role": "sales_agent"},
    )
    try:
        async with database.session_factory() as session, session.begin():
            session.add_all([owner, member])
            await session.flush()
            session.add_all([Profile(user_id=owner.id), Profile(user_id=member.id), organization])
            await session.flush()
            session.add(owner_membership)

        async with database.session_factory() as session:
            invited = await service.invite_member(
                session,
                actor_user_id=owner.id,
                organization_id=organization.id,
                target_user_id=member.id,
                role="sales_agent",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        assert invited.status == "invited"
        assert invited.id in delivery.tokens

        async with database.session_factory() as session:
            accepted = await service.accept_invitation(
                session,
                actor_user_id=member.id,
                membership_id=invited.id,
                invitation_token=delivery.tokens[invited.id],
            )
        assert accepted.status == "active"
        assert accepted.version == 2

        async with database.session_factory() as session:
            memberships = await service.list_memberships(session, actor_user_id=member.id)
        assert [item.id for item in memberships] == [invited.id]

        async with database.session_factory() as session:
            left = await service.leave_organization(
                session,
                actor_user_id=member.id,
                membership_id=invited.id,
                expected_version=accepted.version,
            )
        assert left.status == "left"
        assert left.version == 3
        assert owner.id in cache.invalidated
        assert member.id in cache.invalidated

        async with database.session_factory() as session:
            actions = set(
                (
                    await session.scalars(
                        select(AuditLog.action).where(AuditLog.resource_id == invited.id)
                    )
                ).all()
            )
        assert {
            "dealer.membership.invited",
            "dealer.membership.accepted",
            "dealer.membership.left",
        } <= actions
    finally:
        await database.close()
