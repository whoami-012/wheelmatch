from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Protocol, cast
from uuid import UUID

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class AuthorizationCache(Protocol):
    async def get(self, user_id: UUID, *, expected_version: int) -> dict[str, Any] | None: ...

    async def set(self, user_id: UUID, *, version: int, projection: dict[str, Any]) -> None: ...

    async def invalidate(self, user_ids: Iterable[UUID]) -> None: ...


class RedisAuthorizationCache:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(user_id: UUID) -> str:
        return f"authz:user:{user_id}"

    async def get(self, user_id: UUID, *, expected_version: int) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(self._key(user_id))
        except Exception as exc:
            logger.warning("authorization_cache_read_failed", error_type=type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            value = cast(dict[str, Any], json.loads(raw))
            if value.get("version") != expected_version:
                return None
            projection = value.get("projection")
            return cast(dict[str, Any], projection) if isinstance(projection, dict) else None
        except (TypeError, ValueError):
            return None

    async def set(self, user_id: UUID, *, version: int, projection: dict[str, Any]) -> None:
        payload = json.dumps(
            {"version": version, "projection": projection}, separators=(",", ":"), sort_keys=True
        )
        try:
            await self._redis.set(self._key(user_id), payload, ex=self._ttl_seconds)
        except Exception as exc:
            logger.warning("authorization_cache_write_failed", error_type=type(exc).__name__)

    async def invalidate(self, user_ids: Iterable[UUID]) -> None:
        keys = [self._key(user_id) for user_id in dict.fromkeys(user_ids)]
        if not keys:
            return
        try:
            await self._redis.delete(*keys)
        except Exception as exc:
            logger.warning("authorization_cache_invalidation_failed", error_type=type(exc).__name__)


class NullAuthorizationCache:
    async def get(self, user_id: UUID, *, expected_version: int) -> dict[str, Any] | None:
        del user_id, expected_version
        return None

    async def set(self, user_id: UUID, *, version: int, projection: dict[str, Any]) -> None:
        del user_id, version, projection

    async def invalidate(self, user_ids: Iterable[UUID]) -> None:
        tuple(user_ids)
