from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.core.config import Environment, MediaScannerProvider, Settings


class ScanStatus(StrEnum):
    CLEAN = "clean"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ScanResult:
    status: ScanStatus


class ScannerTransientError(Exception):
    """A retryable scanner availability failure."""


class ScannerPermanentError(Exception):
    """A non-retryable scanner integration failure."""


class MalwareScanner(Protocol):
    async def scan(self, content: bytes) -> ScanResult: ...


class DisabledMalwareScanner:
    """Development/test-only no-op adapter."""

    async def scan(self, content: bytes) -> ScanResult:
        del content
        return ScanResult(status=ScanStatus.CLEAN)


class DeterministicMalwareScanner:
    """Deterministic adapter for unit and local integration tests."""

    def __init__(
        self,
        *,
        result: ScanStatus = ScanStatus.CLEAN,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error

    async def scan(self, content: bytes) -> ScanResult:
        del content
        if self._error is not None:
            raise self._error
        return ScanResult(status=self._result)


def build_malware_scanner(settings: Settings) -> MalwareScanner:
    if settings.environment not in {Environment.LOCAL, Environment.TEST}:
        raise RuntimeError("a production-grade media malware scanner is not configured")
    if settings.media_scanner_provider is MediaScannerProvider.DETERMINISTIC:
        return DeterministicMalwareScanner()
    return DisabledMalwareScanner()
