from __future__ import annotations

import asyncio
from datetime import timedelta

import structlog

from app.core.config import get_settings
from app.core.database import Database
from app.core.outbox import OutboxRelay, SqsEventPublisher
from app.core.telemetry import configure_logging, configure_sentry

logger = structlog.get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    configure_sentry(settings)
    database = Database.create(settings)
    relay = OutboxRelay(
        session_factory=database.session_factory,
        publisher=SqsEventPublisher(settings),
        batch_size=settings.outbox_batch_size,
    )
    logger.info("outbox_relay_started", **settings.safe_summary())
    try:
        await relay.recover_stale_claims(older_than=timedelta(minutes=5))
        while True:
            published = await relay.run_once()
            if published == 0:
                await asyncio.sleep(settings.outbox_poll_interval_seconds)
    finally:
        await database.close()
        logger.info("outbox_relay_stopped")


if __name__ == "__main__":
    asyncio.run(run())
