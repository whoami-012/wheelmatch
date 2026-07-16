from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.outbox.models import OutboxEvent


def enqueue_event(
    session: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, Any],
    schema_version: int = 1,
    traceparent: str | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        schema_version=schema_version,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        traceparent=traceparent,
    )
    session.add(event)
    return event
