from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.core.config import Environment, Settings
from app.core.events import EventEnvelope
from app.core.outbox.publisher import SqsEventPublisher
from app.workers.sqs import SqsConsumer


class FakeSqsClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []

    def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)

    def receive_message(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Messages": [{"Body": "{}", "ReceiptHandle": "receipt-1"}]}

    def delete_message(self, **kwargs: Any) -> None:
        self.deleted.append(kwargs)


def event() -> EventEnvelope:
    return EventEnvelope(
        event_type="system.smoke_test",
        aggregate_type="system",
        aggregate_id=UUID("018f0000-0000-7000-8000-000000000001"),
        occurred_at=datetime.now(UTC),
        payload={"synthetic": True},
    )


@pytest.mark.asyncio
async def test_sqs_publisher_sends_versioned_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeSqsClient()
    monkeypatch.setattr("app.core.outbox.publisher.boto3.client", lambda *args, **kwargs: client)
    publisher = SqsEventPublisher(Settings(environment=Environment.TEST))

    await publisher.publish(event())

    body = json.loads(client.sent[0]["MessageBody"])
    assert body["event_type"] == "system.smoke_test"
    assert client.sent[0]["MessageAttributes"]["schema_version"]["StringValue"] == "1"


@pytest.mark.asyncio
async def test_sqs_consumer_receives_and_acknowledges(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeSqsClient()
    monkeypatch.setattr("app.workers.sqs.boto3.client", lambda *args, **kwargs: client)
    consumer = SqsConsumer(Settings(environment=Environment.TEST))

    messages = [message async for message in consumer.receive()]
    await consumer.acknowledge("receipt-1")

    assert messages[0]["ReceiptHandle"] == "receipt-1"
    assert client.deleted[0]["ReceiptHandle"] == "receipt-1"
