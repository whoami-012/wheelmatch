from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol, cast

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class Probe(Protocol):
    async def check(self) -> None: ...


@dataclass(slots=True)
class DatabaseProbe:
    engine: AsyncEngine

    async def check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            await connection.execute(text("SELECT PostGIS_Version()"))


@dataclass(slots=True)
class RedisProbe:
    client: Redis

    async def check(self) -> None:
        result = await cast(Awaitable[bool], self.client.ping())
        if not result:
            raise RuntimeError("Redis ping returned false")


@dataclass(slots=True)
class HealthService:
    probes: dict[str, Probe]
    timeout_seconds: float

    async def readiness(self) -> dict[str, str]:
        results = await asyncio.gather(
            *(self._run_probe(name, probe) for name, probe in sorted(self.probes.items()))
        )
        return dict(results)

    async def _run_probe(self, name: str, probe: Probe) -> tuple[str, str]:
        try:
            await asyncio.wait_for(probe.check(), timeout=self.timeout_seconds)
        except TimeoutError:
            return name, "timeout"
        except Exception:
            return name, "unavailable"
        return name, "ok"
