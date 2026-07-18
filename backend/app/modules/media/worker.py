from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.events import EventEnvelope
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.modules.audit import AuditRecorder
from app.modules.media.models import ListingMedia, MediaDerivative, MediaProcessingEvidence
from app.modules.media.processing import (
    ImageRejectedError,
    ImageSanitizer,
    SanitizedImageSet,
    ValidatedImageSource,
    validate_source_object,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.scanner import (
    MalwareScanner,
    ScannerPermanentError,
    ScannerTransientError,
    ScanStatus,
)
from app.modules.media.storage import MediaStorage

_PROCESSOR_VERSION = "image-sanitizer-v1"


class MediaWorkerResult(StrEnum):
    COMPLETED = "completed"
    STALE = "stale"
    IN_PROGRESS = "in_progress"
    RETRY = "retry"


class MediaProcessingRequested(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_id: UUID
    listing_id: UUID
    processing_version: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class ProcessingClaim:
    media_id: UUID
    listing_id: UUID
    processing_version: int
    evidence_id: UUID
    claim_token: UUID
    object_key: str
    expected_content_type: str
    expected_size_bytes: int
    expected_checksum_sha256: str | None
    attempt_count: int


@dataclass(slots=True)
class ProcessingDetails:
    input_checksum_sha256: str | None = None
    detected_content_type: str | None = None
    source_format: str | None = None
    source_width: int | None = None
    source_height: int | None = None
    sanitized_checksum_sha256: str | None = None
    perceptual_hash: str | None = None
    scanner_status: str | None = None


def processing_request_is_current(media: ListingMedia, request: MediaProcessingRequested) -> bool:
    return (
        media.id == request.media_id
        and media.listing_id == request.listing_id
        and media.processing_version == request.processing_version
        and media.status in {"processing", "scanning"}
    )


class MediaProcessingWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        repository: MediaRepository,
        storage: MediaStorage,
        scanner: MalwareScanner,
        audit: AuditRecorder,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._repository = repository
        self._storage = storage
        self._scanner = scanner
        self._audit = audit
        self._sanitizer = ImageSanitizer(
            max_pixels=settings.media_processing_max_pixels,
            max_dimension=settings.media_processing_max_dimension,
            max_output_dimension=settings.media_processing_max_output_dimension,
        )

    async def process(
        self, envelope: EventEnvelope, *, receive_count: int = 1
    ) -> MediaWorkerResult:
        try:
            request = MediaProcessingRequested.model_validate(envelope.payload)
        except ValidationError:
            return MediaWorkerResult.STALE
        if envelope.aggregate_id != request.media_id:
            return MediaWorkerResult.STALE
        claim_or_result = await self._claim(request)
        if isinstance(claim_or_result, MediaWorkerResult):
            return claim_or_result
        claim = claim_or_result
        details = ProcessingDetails()

        try:
            stored = await self._storage.download_quarantine(
                claim.object_key,
                max_bytes=self._settings.media_processing_max_input_bytes,
            )
            details.input_checksum_sha256 = stored.checksum_sha256
            source = validate_source_object(
                stored,
                expected_content_type=claim.expected_content_type,
                expected_size_bytes=claim.expected_size_bytes,
                expected_checksum_sha256=claim.expected_checksum_sha256,
                max_input_bytes=self._settings.media_processing_max_input_bytes,
            )
            details.detected_content_type = source.content_type
            details.source_format = source.source_format
            scan = await self._scanner.scan(source.content)
            details.scanner_status = scan.status.value
            if scan.status is ScanStatus.REJECTED:
                raise ImageRejectedError("MALWARE_DETECTED")
            sanitized = await asyncio.to_thread(self._sanitize, source, details)
        except ImageRejectedError as exc:
            await self._finalize_failure(
                claim,
                envelope,
                details=details,
                state="rejected",
                failure_code=exc.failure_code,
            )
            return MediaWorkerResult.COMPLETED
        except ScannerPermanentError:
            details.scanner_status = "error"
            await self._finalize_failure(
                claim,
                envelope,
                details=details,
                state="failed",
                failure_code="SCANNER_PERMANENT_FAILURE",
            )
            return MediaWorkerResult.COMPLETED
        except ScannerTransientError:
            details.scanner_status = "error"
            return await self._retry_or_fail(
                claim,
                envelope,
                details=details,
                receive_count=receive_count,
                failure_code="SCANNER_RETRY_EXHAUSTED",
            )
        except Exception:
            return await self._retry_or_fail(
                claim,
                envelope,
                details=details,
                receive_count=receive_count,
                failure_code="PROCESSING_RETRY_EXHAUSTED",
            )

        object_keys = self._derivative_keys(claim, sanitized)
        try:
            await asyncio.gather(
                *(
                    self._storage.put_private_derivative(
                        object_key=object_key,
                        content=derivative.content,
                        content_type=derivative.content_type,
                    )
                    for object_key, derivative in zip(
                        object_keys, sanitized.derivatives, strict=True
                    )
                )
            )
        except Exception:
            await self._storage.delete_private_derivatives(object_keys)
            return await self._retry_or_fail(
                claim,
                envelope,
                details=details,
                receive_count=receive_count,
                failure_code="STORAGE_RETRY_EXHAUSTED",
            )

        finalized = await self._finalize_success(
            claim,
            envelope,
            details=details,
            sanitized=sanitized,
            object_keys=object_keys,
        )
        if not finalized:
            await self._storage.delete_private_derivatives(object_keys)
            return MediaWorkerResult.STALE
        return MediaWorkerResult.COMPLETED

    def _sanitize(
        self, source: ValidatedImageSource, details: ProcessingDetails
    ) -> SanitizedImageSet:
        sanitized = self._sanitizer.sanitize(source)
        details.source_width = sanitized.source_width
        details.source_height = sanitized.source_height
        details.sanitized_checksum_sha256 = sanitized.sanitized_checksum_sha256
        details.perceptual_hash = sanitized.perceptual_hash
        return sanitized

    async def _claim(
        self, request: MediaProcessingRequested
    ) -> ProcessingClaim | MediaWorkerResult:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            media = await self._repository.get(session, request.media_id, for_update=True)
            if media is None or not processing_request_is_current(media, request):
                return MediaWorkerResult.STALE
            expected_key = f"quarantine/listings/{request.listing_id}/{request.media_id}"
            if media.object_key != expected_key:
                return MediaWorkerResult.STALE
            evidence = await self._repository.get_evidence(
                session,
                request.media_id,
                request.processing_version,
                for_update=True,
            )
            if evidence is not None and evidence.status != "processing":
                return MediaWorkerResult.STALE
            if evidence is not None and evidence.lease_expires_at > now:
                return MediaWorkerResult.IN_PROGRESS
            claim_token = uuid7()
            if evidence is None:
                evidence = MediaProcessingEvidence(
                    id=uuid7(),
                    media_id=media.id,
                    processing_version=media.processing_version,
                    processor_version=_PROCESSOR_VERSION,
                    status="processing",
                    attempt_count=1,
                    claim_token=claim_token,
                    claimed_at=now,
                    lease_expires_at=now
                    + timedelta(seconds=self._settings.media_processing_lease_seconds),
                )
                session.add(evidence)
            else:
                evidence.attempt_count += 1
                evidence.claim_token = claim_token
                evidence.claimed_at = now
                evidence.lease_expires_at = now + timedelta(
                    seconds=self._settings.media_processing_lease_seconds
                )
            media.status = "scanning"
            return ProcessingClaim(
                media_id=media.id,
                listing_id=media.listing_id,
                processing_version=media.processing_version,
                evidence_id=evidence.id,
                claim_token=claim_token,
                object_key=media.object_key,
                expected_content_type=media.expected_content_type,
                expected_size_bytes=media.expected_size_bytes,
                expected_checksum_sha256=media.expected_checksum_sha256,
                attempt_count=evidence.attempt_count,
            )

    async def _finalize_success(
        self,
        claim: ProcessingClaim,
        envelope: EventEnvelope,
        *,
        details: ProcessingDetails,
        sanitized: SanitizedImageSet,
        object_keys: list[str],
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            current = await self._lock_current_claim(session, claim)
            if current is None:
                return False
            media, evidence = current
            for object_key, derivative in zip(object_keys, sanitized.derivatives, strict=True):
                session.add(
                    MediaDerivative(
                        media_id=media.id,
                        evidence_id=evidence.id,
                        processing_version=media.processing_version,
                        kind=derivative.kind,
                        object_key=object_key,
                        content_type=derivative.content_type,
                        width=derivative.width,
                        height=derivative.height,
                        size_bytes=len(derivative.content),
                        checksum_sha256=derivative.checksum_sha256,
                    )
                )
            self._apply_details(evidence, details)
            evidence.status = "moderation_pending"
            evidence.claim_token = None
            evidence.completed_at = now
            media.status = "moderation_pending"
            media.failure_code = None
            media.processed_at = now
            self._record_terminal(
                session,
                envelope,
                media,
                state="moderation_pending",
                failure_code=None,
                derivative_count=len(sanitized.derivatives),
            )
        return True

    async def _finalize_failure(
        self,
        claim: ProcessingClaim,
        envelope: EventEnvelope,
        *,
        details: ProcessingDetails,
        state: str,
        failure_code: str,
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            current = await self._lock_current_claim(session, claim)
            if current is None:
                return False
            media, evidence = current
            self._apply_details(evidence, details)
            evidence.status = state
            evidence.failure_code = failure_code
            evidence.claim_token = None
            evidence.completed_at = now
            media.status = state
            media.failure_code = failure_code
            media.processed_at = now
            self._record_terminal(
                session,
                envelope,
                media,
                state=state,
                failure_code=failure_code,
                derivative_count=0,
            )
        return True

    async def _retry_or_fail(
        self,
        claim: ProcessingClaim,
        envelope: EventEnvelope,
        *,
        details: ProcessingDetails,
        receive_count: int,
        failure_code: str,
    ) -> MediaWorkerResult:
        if max(claim.attempt_count, receive_count) >= self._settings.media_processing_max_attempts:
            await self._finalize_failure(
                claim,
                envelope,
                details=details,
                state="failed",
                failure_code=failure_code,
            )
            return MediaWorkerResult.RETRY
        async with self._session_factory() as session, session.begin():
            current = await self._lock_current_claim(session, claim)
            if current is None:
                return MediaWorkerResult.STALE
            media, evidence = current
            self._apply_details(evidence, details)
            evidence.claim_token = None
            evidence.lease_expires_at = datetime.now(UTC)
            media.status = "processing"
        return MediaWorkerResult.RETRY

    async def _lock_current_claim(
        self, session: AsyncSession, claim: ProcessingClaim
    ) -> tuple[ListingMedia, MediaProcessingEvidence] | None:
        media = await self._repository.get(session, claim.media_id, for_update=True)
        evidence = await self._repository.get_evidence(
            session, claim.media_id, claim.processing_version, for_update=True
        )
        if (
            media is None
            or evidence is None
            or media.processing_version != claim.processing_version
            or media.status not in {"processing", "scanning"}
            or evidence.status != "processing"
            or evidence.claim_token != claim.claim_token
        ):
            return None
        return media, evidence

    @staticmethod
    def _apply_details(evidence: MediaProcessingEvidence, details: ProcessingDetails) -> None:
        evidence.input_checksum_sha256 = details.input_checksum_sha256
        evidence.detected_content_type = details.detected_content_type
        evidence.source_format = details.source_format
        evidence.source_width = details.source_width
        evidence.source_height = details.source_height
        evidence.sanitized_checksum_sha256 = details.sanitized_checksum_sha256
        evidence.perceptual_hash = details.perceptual_hash
        evidence.scanner_status = details.scanner_status

    def _record_terminal(
        self,
        session: AsyncSession,
        envelope: EventEnvelope,
        media: ListingMedia,
        *,
        state: str,
        failure_code: str | None,
        derivative_count: int,
    ) -> None:
        changes: dict[str, str | int] = {
            "status": state,
            "processing_version": media.processing_version,
            "derivative_count": derivative_count,
        }
        if failure_code is not None:
            changes["failure_code"] = failure_code
        self._audit.record(
            session,
            action=f"listing.media.sanitization_{state}",
            outcome="success" if state == "moderation_pending" else "failure",
            reason_code=failure_code,
            resource_type="listing_media",
            resource_id=media.id,
            changes=changes,
            trace_id=envelope.traceparent,
        )
        payload: dict[str, str | int] = {
            "media_id": str(media.id),
            "listing_id": str(media.listing_id),
            "processing_version": media.processing_version,
            "state": state,
        }
        if failure_code is not None:
            payload["failure_code"] = failure_code
        enqueue_event(
            session,
            event_type=f"media.sanitization.{state}",
            aggregate_type="listing_media",
            aggregate_id=media.id,
            payload=payload,
            traceparent=envelope.traceparent,
        )

    @staticmethod
    def _derivative_keys(claim: ProcessingClaim, sanitized: SanitizedImageSet) -> list[str]:
        prefix = (
            f"private/derivatives/listings/{claim.listing_id}/{claim.media_id}/"
            f"v{claim.processing_version}"
        )
        return [f"{prefix}/{derivative.kind}.jpg" for derivative in sanitized.derivatives]
