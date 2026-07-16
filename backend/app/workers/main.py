from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import Database
from app.core.events import EventEnvelope
from app.core.telemetry import configure_logging, configure_sentry
from app.workers.consumer import SqlEventProcessor
from app.workers.sqs import SqsConsumer

logger = structlog.get_logger(__name__)


async def handle_smoke_test(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Phase 0 handler used to verify the durable consumer boundary."""
    del session
    logger.info("synthetic_event_processed", event_id=str(envelope.event_id))


async def run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    configure_sentry(settings)
    database = Database.create(settings)
    processor = SqlEventProcessor(
        consumer_name="foundation-worker-v1",
        session_factory=database.session_factory,
        handlers={"system.smoke_test": handle_smoke_test},
    )
    consumer = SqsConsumer(settings)
    logger.info("worker_started", **settings.safe_summary())
    try:
        while True:
            async for message in consumer.receive():
                await _process_message(message, processor, consumer)
    finally:
        await database.close()
        logger.info("worker_stopped")


async def _process_message(
    message: dict[str, Any],
    processor: SqlEventProcessor,
    consumer: SqsConsumer,
) -> None:
    try:
        envelope = EventEnvelope.model_validate(json.loads(message["Body"]))
    except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("invalid_event_envelope", error_type=type(exc).__name__)
        return
    if not processor.supports(envelope.event_type):
        logger.warning(
            "unsupported_event_type",
            event_id=str(envelope.event_id),
            event_type=envelope.event_type,
        )
        return
    await processor.process(envelope)
    await consumer.acknowledge(message["ReceiptHandle"])


if __name__ == "__main__":
    asyncio.run(run())
