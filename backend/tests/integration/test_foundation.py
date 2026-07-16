from __future__ import annotations

import os
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, Settings
from app.core.database import Database
from app.core.events import EventEnvelope
from app.core.idempotency import (
    IdempotencyConflictError,
    IdempotencyRepository,
    canonical_request_hash,
)
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent, OutboxRelay, enqueue_event
from app.workers.consumer import ProcessingResult, SqlEventProcessor

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


@pytest.mark.asyncio
async def test_migration_created_postgis_and_foundation_tables() -> None:
    database = Database.create(integration_settings())
    try:
        async with database.engine.connect() as connection:
            postgis_version = (
                await connection.execute(text("SELECT PostGIS_Version()"))
            ).scalar_one()
            table_names = {
                row[0]
                for row in (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                )
            }
        assert postgis_version
        assert {
            "alembic_version",
            "consumer_events",
            "idempotency_keys",
            "outbox_events",
        } <= table_names
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_redis_is_available() -> None:
    settings = integration_settings()
    redis: Redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    try:
        assert await cast(Awaitable[bool], redis.ping())
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_duplicate_synthetic_event_is_processed_once() -> None:
    database = Database.create(integration_settings())
    handled = 0

    async def handler(_session: AsyncSession, _event: EventEnvelope) -> None:
        nonlocal handled
        handled += 1

    event = EventEnvelope(
        event_type="system.smoke_test",
        aggregate_type="system",
        aggregate_id=UUID("018f0000-0000-7000-8000-000000000001"),
        occurred_at=datetime.now(UTC),
        payload={"synthetic": True},
    )
    processor = SqlEventProcessor(
        consumer_name=f"integration-{event.event_id}",
        session_factory=database.session_factory,
        handlers={event.event_type: handler},
    )
    try:
        first = await processor.process(event)
        second = await processor.process(event)
    finally:
        await database.close()

    assert first is ProcessingResult.PROCESSED
    assert second is ProcessingResult.DUPLICATE
    assert handled == 1


@pytest.mark.asyncio
async def test_idempotency_reservation_replays_and_rejects_payload_mismatch() -> None:
    database = Database.create(integration_settings())
    repository = IdempotencyRepository()
    key = str(uuid7())
    request_hash = canonical_request_hash(method="POST", path="/synthetic", payload={"a": 1})
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    try:
        async with database.session_factory() as session, session.begin():
            first = await repository.reserve(
                session,
                scope="integration",
                operation="synthetic.create",
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            await repository.complete(
                session,
                scope="integration",
                operation="synthetic.create",
                key=key,
                response_status=201,
                response_body={"created": True},
            )
        async with database.session_factory() as session, session.begin():
            replay = await repository.reserve(
                session,
                scope="integration",
                operation="synthetic.create",
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
        assert first.acquired
        assert not replay.acquired
        assert replay.replay_status == 201
        assert replay.replay_body == {"created": True}
        async with database.session_factory() as session, session.begin():
            with pytest.raises(IdempotencyConflictError):
                await repository.reserve(
                    session,
                    scope="integration",
                    operation="synthetic.create",
                    key=key,
                    request_hash="0" * 64,
                    expires_at=expires_at,
                )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_outbox_relay_publishes_committed_event() -> None:
    database = Database.create(integration_settings())
    published: list[EventEnvelope] = []

    class Publisher:
        async def publish(self, envelope: EventEnvelope) -> None:
            published.append(envelope)

    aggregate_id = uuid7()
    async with database.session_factory() as session, session.begin():
        event_record = enqueue_event(
            session,
            event_type="system.smoke_test",
            aggregate_type="system",
            aggregate_id=aggregate_id,
            payload={"synthetic": True},
        )
    relay = OutboxRelay(
        session_factory=database.session_factory,
        publisher=Publisher(),
        batch_size=10,
    )
    try:
        assert await relay.run_once() >= 1
        async with database.session_factory() as session:
            stored = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.id == event_record.id))
            ).scalar_one()
        assert stored.status == "published"
        assert any(envelope.aggregate_id == aggregate_id for envelope in published)
    finally:
        await database.close()
