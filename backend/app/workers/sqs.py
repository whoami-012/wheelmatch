from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import boto3

from app.core.config import Settings


class SqsConsumer:
    def __init__(self, settings: Settings) -> None:
        self._queue_url = settings.sqs_events_queue_url.get_secret_value()
        self._wait_time_seconds = settings.worker_wait_time_seconds
        self._client = boto3.client(
            "sqs",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
        )

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        response = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=self._wait_time_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            yield message

    async def acknowledge(self, receipt_handle: str) -> None:
        await asyncio.to_thread(
            self._client.delete_message,
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )
