from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.events import EventEnvelope
from app.core.outbox.models import ConsumerEvent

EventHandler = Callable[[AsyncSession, EventEnvelope], Awaitable[None]]


class ProcessingResult(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"


class SqlEventProcessor:
    """Processes an event and records its consumer marker in one transaction."""

    def __init__(
        self,
        *,
        consumer_name: str,
        session_factory: async_sessionmaker[AsyncSession],
        handlers: dict[str, EventHandler],
    ) -> None:
        self._consumer_name = consumer_name
        self._session_factory = session_factory
        self._handlers = handlers

    def supports(self, event_type: str) -> bool:
        return event_type in self._handlers

    async def process(self, envelope: EventEnvelope) -> ProcessingResult:
        handler = self._handlers[envelope.event_type]
        async with self._session_factory() as session, session.begin():
            claimed = (
                await session.execute(
                    insert(ConsumerEvent)
                    .values(
                        consumer_name=self._consumer_name,
                        event_id=envelope.event_id,
                        event_type=envelope.event_type,
                    )
                    .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
                    .returning(ConsumerEvent.id)
                )
            ).scalar_one_or_none()
            if claimed is None:
                return ProcessingResult.DUPLICATE
            await handler(session, envelope)
        return ProcessingResult.PROCESSED
