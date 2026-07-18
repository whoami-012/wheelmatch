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
from app.core.config.settings import IdentityVerificationProviderName
from app.core.database import Database, get_session
from app.core.errors import AppError
from app.core.errors.handlers import install_exception_handlers
from app.core.idempotency import IdempotencyRepository, canonical_request_hash
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.core.outbox.service import enqueue_event
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.dealers import models as dealer_models  # noqa: F401
from app.modules.identity.dependencies import (
    get_authentication_rate_limiter,
    get_current_principal,
)
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.verification.dependencies import get_identity_verification_service
from app.modules.verification.models import IdentityVerification, UserVerificationState
from app.modules.verification.provider import DeterministicIdentityVerificationProvider
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.router import router as verification_router
from app.modules.verification.service import IdentityVerificationService

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
        identity_verification_provider=IdentityVerificationProviderName.DETERMINISTIC,
    )


def make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid7(),
        normalized_email=f"phase3-verification-{uuid7()}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-plaintext",
        status="active",
        email_verified_at=now,
        phone_verified_at=now,
        password_changed_at=now,
    )


def build_service(
    *,
    audit: AuditRecorder | None = None,
    event_writer: Any = enqueue_event,
) -> IdentityVerificationService:
    return IdentityVerificationService(
        repository=VerificationRepository(),
        identity_repository=IdentityRepository(),
        provider=DeterministicIdentityVerificationProvider(),
        audit=audit or AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
        event_writer=event_writer,
    )


def start_hash() -> str:
    return canonical_request_hash(
        method="POST", path="/api/v1/me/identity-verifications", payload={}
    )


async def add_user(database: Database) -> User:
    user = make_user()
    async with database.session_factory() as session, session.begin():
        session.add(user)
    return user


async def start_attempt(
    database: Database, user: User, *, key: str
) -> tuple[IdentityVerificationService, Any]:
    service = build_service()
    async with database.session_factory() as session:
        response = await service.start(
            session,
            actor_user_id=user.id,
            idempotency_key=key,
            request_hash=start_hash(),
        )
    return service, response


@pytest.mark.asyncio
async def test_start_is_idempotent_concurrent_and_capture_url_is_not_persisted() -> None:
    database = Database.create(integration_settings())
    try:
        user = await add_user(database)
        service, first = await start_attempt(database, user, key=f"verify-start-{uuid7()}")
        same_key = f"verify-replay-{uuid7()}"
        async with database.session_factory() as session:
            replay_first = await service.start(
                session,
                actor_user_id=user.id,
                idempotency_key=same_key,
                request_hash=start_hash(),
            )
        async with database.session_factory() as session:
            replay_second = await service.start(
                session,
                actor_user_id=user.id,
                idempotency_key=same_key,
                request_hash=start_hash(),
            )
        concurrent = await asyncio.gather(
            start_attempt(database, user, key=f"verify-concurrent-a-{uuid7()}"),
            start_attempt(database, user, key=f"verify-concurrent-b-{uuid7()}"),
        )

        assert first.capture_url is not None
        assert replay_first.attempt_id == replay_second.attempt_id == first.attempt_id
        assert {item[1].attempt_id for item in concurrent} == {first.attempt_id}
        async with database.session_factory() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(IdentityVerification).where(IdentityVerification.user_id == user.id)
                    )
                ).all()
            )
            active_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(IdentityVerification)
                    .where(
                        IdentityVerification.user_id == user.id,
                        IdentityVerification.status.in_(
                            ("session_pending", "pending", "manual_review")
                        ),
                    )
                )
                or 0
            )
            audits = list(
                (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.resource_id == first.attempt_id)
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.payload["attempt_id"].astext == str(first.attempt_id)
                        )
                    )
                ).all()
            )
        serialized = json.dumps(
            {
                "attempts": [
                    {
                        column.name: getattr(attempt, column.name)
                        for column in IdentityVerification.__table__.columns
                    }
                    for attempt in attempts
                ],
                "audits": [audit.changes for audit in audits],
                "events": [event.payload for event in events],
            },
            default=str,
        )
        assert len(attempts) == 1
        assert active_count == 1
        assert "capture_url" not in serialized
        assert "verify.local.test" not in serialized
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_results_are_atomic_idempotent_safe_and_preserve_history() -> None:
    database = Database.create(integration_settings())
    provider = DeterministicIdentityVerificationProvider()
    try:
        user = await add_user(database)
        service, started = await start_attempt(database, user, key=f"verify-result-{uuid7()}")
        verified_at = datetime.now(UTC)
        result = provider.result(
            attempt_id=started.attempt_id,
            event_id=f"verified-{uuid7()}",
            status="verified",
            assurance_level="standard",
            verified_at=verified_at,
            expires_at=verified_at + timedelta(days=365),
        )
        async with database.session_factory() as session:
            applied = await service.apply_provider_result(session, result)
        async with database.session_factory() as session:
            duplicate = await service.apply_provider_result(session, result)
        assert applied.disposition == "applied"
        assert duplicate.disposition == "duplicate"

        async with database.session_factory() as session:
            attempt = await session.get(IdentityVerification, started.attempt_id)
            state = await session.get(UserVerificationState, user.id)
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.resource_id == started.attempt_id,
                    AuditLog.action == "identity.verification.state_changed",
                    AuditLog.changes["status"].astext == "verified",
                )
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.payload["attempt_id"].astext == str(started.attempt_id),
                    OutboxEvent.payload["state"].astext == "verified",
                )
            )
        assert attempt is not None and attempt.status == "verified"
        assert state is not None and state.effective_status == "verified"
        assert state.current_attempt_id == attempt.id
        assert audit is not None and event is not None
        assert set(event.payload) == {
            "user_id",
            "attempt_id",
            "projection_version",
            "state",
            "expiry",
            "failure_code",
        }
        assert "provider" not in json.dumps({"audit": audit.changes, "event": event.payload})

        with pytest.raises(AppError, match="conflicts"):
            async with database.session_factory() as session:
                await service.apply_provider_result(
                    session,
                    provider.result(
                        attempt_id=started.attempt_id,
                        event_id=f"conflict-{uuid7()}",
                        status="failed",
                        safe_failure_code="PROVIDER_REJECTED",
                    ),
                )

        second_user = await add_user(database)
        second_service, failed_start = await start_attempt(
            database, second_user, key=f"verify-failed-{uuid7()}"
        )
        async with database.session_factory() as session:
            failed = await second_service.apply_provider_result(
                session,
                provider.result(
                    attempt_id=failed_start.attempt_id,
                    event_id=f"failed-{uuid7()}",
                    status="failed",
                    safe_failure_code="provider-internal-reason",
                ),
            )
        assert failed.status == "failed"
        _, later = await start_attempt(database, second_user, key=f"verify-later-{uuid7()}")
        async with database.session_factory() as session:
            stale = await second_service.apply_provider_result(
                session,
                provider.result(
                    attempt_id=failed_start.attempt_id,
                    event_id=f"stale-{uuid7()}",
                    status="verified",
                    assurance_level="standard",
                    verified_at=verified_at,
                    expires_at=verified_at + timedelta(days=30),
                ),
            )
            history = list(
                (
                    await session.scalars(
                        select(IdentityVerification)
                        .where(IdentityVerification.user_id == second_user.id)
                        .order_by(IdentityVerification.attempt_number)
                    )
                ).all()
            )
            current = await session.get(UserVerificationState, second_user.id)
        assert stale.disposition == "stale"
        assert [attempt.id for attempt in history] == [failed_start.attempt_id, later.attempt_id]
        assert history[0].status == "failed"
        assert history[0].safe_failure_code == "VERIFICATION_FAILED"
        assert current is not None and current.current_attempt_id == later.attempt_id
        assert current.effective_status == "pending"
    finally:
        await database.close()


@pytest.mark.parametrize(
    "result_status",
    [
        pytest.param("manual_review", id="manual-review"),
        pytest.param("failed", id="failed"),
    ],
)
@pytest.mark.asyncio
async def test_failed_and_manual_review_results_store_only_safe_state(result_status: str) -> None:
    database = Database.create(integration_settings())
    provider = DeterministicIdentityVerificationProvider()
    try:
        user = await add_user(database)
        service, started = await start_attempt(database, user, key=f"verify-safe-{uuid7()}")
        result = provider.result(
            attempt_id=started.attempt_id,
            event_id=f"safe-{uuid7()}",
            status=result_status,
            safe_failure_code="provider-secret-rule" if result_status == "failed" else None,
        )
        async with database.session_factory() as session:
            await service.apply_provider_result(session, result)
        async with database.session_factory() as session:
            attempt = await session.get(IdentityVerification, started.attempt_id)
            state = await session.get(UserVerificationState, user.id)
        assert attempt is not None and state is not None
        expected_code = (
            "MANUAL_REVIEW_REQUIRED" if result_status == "manual_review" else "VERIFICATION_FAILED"
        )
        assert attempt.safe_failure_code == state.safe_failure_code == expected_code
        assert "provider-secret-rule" not in json.dumps(
            {"attempt": attempt.safe_failure_code, "state": state.safe_failure_code}
        )
    finally:
        await database.close()


class FailingAudit(AuditRecorder):
    def record(self, *args: Any, **kwargs: Any) -> Never:
        del args, kwargs
        raise RuntimeError("forced audit failure")


def failing_event_writer(*args: Any, **kwargs: Any) -> Never:
    del args, kwargs
    raise RuntimeError("forced outbox failure")


@pytest.mark.parametrize(
    "failure_target",
    [
        pytest.param("audit", id="audit-rollback"),
        pytest.param("outbox", id="outbox-rollback"),
    ],
)
@pytest.mark.asyncio
async def test_audit_or_outbox_failure_rolls_back_result_finalization(
    failure_target: str,
) -> None:
    database = Database.create(integration_settings())
    provider = DeterministicIdentityVerificationProvider()
    try:
        user = await add_user(database)
        _, started = await start_attempt(database, user, key=f"verify-rollback-{uuid7()}")
        service = build_service(
            audit=FailingAudit() if failure_target == "audit" else AuditRecorder(),
            event_writer=failing_event_writer if failure_target == "outbox" else enqueue_event,
        )
        now = datetime.now(UTC)
        with pytest.raises(RuntimeError, match=f"forced {failure_target} failure"):
            async with database.session_factory() as session:
                await service.apply_provider_result(
                    session,
                    provider.result(
                        attempt_id=started.attempt_id,
                        event_id=f"rollback-{uuid7()}",
                        status="verified",
                        assurance_level="standard",
                        verified_at=now,
                        expires_at=now + timedelta(days=365),
                    ),
                )
        async with database.session_factory() as session:
            attempt = await session.get(IdentityVerification, started.attempt_id)
            state = await session.get(UserVerificationState, user.id)
            verified_audits = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.resource_id == started.attempt_id,
                        AuditLog.changes["status"].astext == "verified",
                    )
                )
                or 0
            )
            verified_events = int(
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.payload["attempt_id"].astext == str(started.attempt_id),
                        OutboxEvent.payload["state"].astext == "verified",
                    )
                )
                or 0
            )
        assert attempt is not None and attempt.status == "pending"
        assert state is not None and state.effective_status == "pending"
        assert verified_audits == verified_events == 0
    finally:
        await database.close()


class AllowLimiter:
    async def enforce(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


@pytest.mark.asyncio
async def test_self_service_apis_expose_allowlisted_state_and_isolate_users() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    try:
        owner = await add_user(database)
        outsider = await add_user(database)
        service = build_service()
        app = FastAPI()
        app.state.settings = settings
        install_exception_handlers(app)
        app.include_router(verification_router)

        async def session_override() -> Any:
            async with database.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_identity_verification_service] = lambda: service
        app.dependency_overrides[get_authentication_rate_limiter] = lambda: AllowLimiter()
        app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(user_id=owner.id)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            started = await client.post(
                "/api/v1/me/identity-verifications",
                headers={"Idempotency-Key": f"verify-api-{uuid7()}"},
            )
            status_response = await client.get("/api/v1/me/identity-verification")
            app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(
                user_id=outsider.id
            )
            outsider_response = await client.get("/api/v1/me/identity-verification")

        assert started.status_code == 201
        assert started.json()["capture_url"].startswith("https://verify.local.test/")
        assert status_response.status_code == 200
        body = status_response.json()
        assert set(body) == {
            "attempt_id",
            "status",
            "assurance_level",
            "verified_at",
            "expires_at",
            "revoked_at",
            "version",
            "failure_code",
            "updated_at",
        }
        assert {
            "provider_reference",
            "provider_result_event_id",
            "capture_url",
            "documents",
            "scores",
            "reason",
        }.isdisjoint(body)
        assert outsider_response.status_code == 404
        assert outsider_response.json()["code"] == "VERIFICATION_NOT_FOUND"
    finally:
        await database.close()
