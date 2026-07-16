from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency.models import IdempotencyRecord


class IdempotencyConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Reservation:
    acquired: bool
    replay_status: int | None = None
    replay_body: dict[str, Any] | None = None


def canonical_request_hash(*, method: str, path: str, payload: Any) -> str:
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class IdempotencyRepository:
    async def reserve(
        self,
        session: AsyncSession,
        *,
        scope: str,
        operation: str,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> Reservation:
        statement = (
            insert(IdempotencyRecord)
            .values(
                scope=scope,
                operation=operation,
                idempotency_key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["scope", "operation", "idempotency_key"])
            .returning(IdempotencyRecord.id)
        )
        created = (await session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return Reservation(acquired=True)

        existing = (
            await session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.scope == scope,
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        ).scalar_one()
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError("idempotency key payload mismatch")
        return Reservation(
            acquired=False,
            replay_status=existing.response_status,
            replay_body=existing.response_body,
        )

    async def complete(
        self,
        session: AsyncSession,
        *,
        scope: str,
        operation: str,
        key: str,
        response_status: int,
        response_body: dict[str, Any],
        resource_type: str | None = None,
        resource_id: Any | None = None,
    ) -> None:
        await session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == key,
                IdempotencyRecord.state == "reserved",
            )
            .values(
                state="completed",
                response_status=response_status,
                response_body=response_body,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
