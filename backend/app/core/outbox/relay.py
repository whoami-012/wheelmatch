from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.events import EventEnvelope
from app.core.outbox.models import OutboxEvent
from app.core.outbox.publisher import EventPublisher

logger = structlog.get_logger(__name__)


class OutboxRelay:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        batch_size: int,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch_size = batch_size

    async def run_once(self) -> int:
        event_ids = await self._claim_batch()
        for event_id in event_ids:
            await self._publish_one(event_id)
        return len(event_ids)

    async def _claim_batch(self) -> list[UUID]:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent)
                        .where(
                            OutboxEvent.status == "pending",
                            OutboxEvent.available_at <= now,
                        )
                        .order_by(OutboxEvent.available_at, OutboxEvent.id)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for event in events:
                event.status = "publishing"
                event.locked_at = now
                event.attempts += 1
            return [event.id for event in events]

    async def _publish_one(self, event_id: UUID) -> None:
        async with self._session_factory() as session:
            event = await session.get(OutboxEvent, event_id)
            if event is None or event.status != "publishing":
                return
            envelope = EventEnvelope(
                event_id=event.id,
                event_type=event.event_type,
                schema_version=event.schema_version,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                occurred_at=event.occurred_at,
                traceparent=event.traceparent,
                payload=event.payload,
            )

        try:
            await self._publisher.publish(envelope)
        except Exception as exc:
            await self._mark_failed(event_id, type(exc).__name__)
            logger.warning(
                "outbox_publish_failed",
                event_id=str(event_id),
                error_type=type(exc).__name__,
            )
            return
        await self._mark_published(event_id)

    async def _mark_published(self, event_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id, OutboxEvent.status == "publishing")
                .values(
                    status="published",
                    published_at=datetime.now(UTC),
                    locked_at=None,
                    last_error_code=None,
                )
            )

    async def _mark_failed(self, event_id: UUID, error_code: str) -> None:
        async with self._session_factory() as session, session.begin():
            event = await session.get(OutboxEvent, event_id, with_for_update=True)
            if event is None or event.status != "publishing":
                return
            delay_seconds = min(300, 2 ** min(event.attempts, 8))
            event.status = "pending"
            event.available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            event.locked_at = None
            event.last_error_code = error_code[:80]

    async def recover_stale_claims(self, *, older_than: timedelta) -> int:
        threshold = datetime.now(UTC) - older_than
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.status == "publishing",
                    OutboxEvent.locked_at < threshold,
                )
                .values(
                    status="pending",
                    locked_at=None,
                    available_at=datetime.now(UTC),
                    last_error_code="STALE_CLAIM_RECOVERED",
                )
            )
            return int(getattr(result, "rowcount", 0) or 0)
