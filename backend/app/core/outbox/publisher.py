from __future__ import annotations

import asyncio
import json
from typing import Protocol

import boto3

from app.core.config import Settings
from app.core.events import EventEnvelope


class EventPublisher(Protocol):
    async def publish(self, envelope: EventEnvelope) -> None: ...


class SqsEventPublisher:
    def __init__(self, settings: Settings) -> None:
        self._queue_url = settings.sqs_events_queue_url.get_secret_value()
        self._client = boto3.client(
            "sqs",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
        )

    async def publish(self, envelope: EventEnvelope) -> None:
        body = json.dumps(envelope.model_dump(mode="json"), separators=(",", ":"))
        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=self._queue_url,
            MessageBody=body,
            MessageAttributes={
                "event_type": {"DataType": "String", "StringValue": envelope.event_type},
                "schema_version": {
                    "DataType": "Number",
                    "StringValue": str(envelope.schema_version),
                },
            },
        )
