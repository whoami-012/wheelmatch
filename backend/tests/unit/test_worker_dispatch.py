from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from app.core.events import EventEnvelope
from app.workers.consumer import ProcessingResult, SqlEventProcessor
from app.workers.main import _process_message
from app.workers.sqs import SqsConsumer


class FakeProcessor:
    def __init__(self, *, supported: bool = True) -> None:
        self.supported = supported
        self.processed: list[EventEnvelope] = []

    def supports(self, _event_type: str) -> bool:
        return self.supported

    async def process(self, event: EventEnvelope) -> ProcessingResult:
        self.processed.append(event)
        return ProcessingResult.PROCESSED


class FakeConsumer:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []

    async def acknowledge(self, receipt_handle: str) -> None:
        self.acknowledged.append(receipt_handle)


def message() -> dict[str, Any]:
    envelope = EventEnvelope(
        event_type="system.smoke_test",
        aggregate_type="system",
        aggregate_id=UUID("018f0000-0000-7000-8000-000000000001"),
        occurred_at=datetime.now(UTC),
        payload={"synthetic": True},
    )
    return {"Body": json.dumps(envelope.model_dump(mode="json")), "ReceiptHandle": "r-1"}


@pytest.mark.asyncio
async def test_valid_supported_message_is_processed_and_acknowledged() -> None:
    processor = FakeProcessor()
    consumer = FakeConsumer()

    await _process_message(
        message(),
        cast(SqlEventProcessor, processor),
        cast(SqsConsumer, consumer),
    )

    assert len(processor.processed) == 1
    assert consumer.acknowledged == ["r-1"]


@pytest.mark.asyncio
async def test_invalid_or_unsupported_message_is_not_acknowledged() -> None:
    consumer = FakeConsumer()
    invalid_processor = FakeProcessor()
    unsupported_processor = FakeProcessor(supported=False)

    await _process_message(
        {"Body": "not-json", "ReceiptHandle": "r-1"},
        cast(SqlEventProcessor, invalid_processor),
        cast(SqsConsumer, consumer),
    )
    await _process_message(
        message(),
        cast(SqlEventProcessor, unsupported_processor),
        cast(SqsConsumer, consumer),
    )

    assert consumer.acknowledged == []
