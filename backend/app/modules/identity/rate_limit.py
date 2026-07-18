from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import structlog
from redis.asyncio import Redis
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.ids import uuid7
from app.core.security import SecretHasher
from app.modules.identity.models import RateLimitBucket

logger = structlog.get_logger(__name__)

_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    scope: str
    limit: int
    window_seconds: int


class AuthenticationRateLimiter:
    """Redis-first limiter with an atomic PostgreSQL fallback and fail-closed errors."""

    def __init__(self, redis: Redis, *, secret_hasher: SecretHasher) -> None:
        self._redis = redis
        self._secrets = secret_hasher

    async def enforce(
        self,
        session: AsyncSession,
        *,
        rule: RateLimitRule,
        subjects: list[str],
    ) -> None:
        subject_hashes = [self._secrets.digest(subject) for subject in dict.fromkeys(subjects)]
        try:
            counts = [
                int(
                    await cast(
                        Awaitable[str],
                        self._redis.eval(
                            _INCREMENT_SCRIPT,
                            1,
                            f"rate:{rule.scope}:{subject_hash}",
                            rule.window_seconds,
                        ),
                    )
                )
                for subject_hash in subject_hashes
            ]
        except Exception as exc:
            logger.warning("authentication_rate_limit_redis_failed", error_type=type(exc).__name__)
            counts = await self._enforce_database_fallback(
                session, rule=rule, subject_hashes=subject_hashes
            )
        if any(count > rule.limit for count in counts):
            raise AppError(
                status=429,
                code="AUTH_RATE_LIMITED",
                title="Too many requests",
                detail="Try again after the current rate-limit window.",
            )

    async def _enforce_database_fallback(
        self,
        session: AsyncSession,
        *,
        rule: RateLimitRule,
        subject_hashes: list[str],
    ) -> list[int]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=rule.window_seconds)
        counts: list[int] = []
        try:
            async with session.begin():
                for subject_hash in subject_hashes:
                    statement = (
                        insert(RateLimitBucket)
                        .values(
                            id=uuid7(),
                            scope=rule.scope,
                            subject_hash=subject_hash,
                            request_count=1,
                            window_expires_at=expires_at,
                        )
                        .on_conflict_do_update(
                            constraint="uq_rate_limit_scope_subject",
                            set_={
                                "request_count": case(
                                    (RateLimitBucket.window_expires_at <= now, 1),
                                    else_=RateLimitBucket.request_count + 1,
                                ),
                                "window_expires_at": case(
                                    (RateLimitBucket.window_expires_at <= now, expires_at),
                                    else_=RateLimitBucket.window_expires_at,
                                ),
                                "updated_at": now,
                            },
                        )
                        .returning(RateLimitBucket.request_count)
                    )
                    counts.append(int((await session.execute(statement)).scalar_one()))
        except Exception as exc:
            logger.error("authentication_rate_limit_fallback_failed", error_type=type(exc).__name__)
            raise AppError(
                status=503,
                code="AUTH_RATE_LIMIT_UNAVAILABLE",
                title="Authentication is temporarily unavailable",
            ) from exc
        return counts
