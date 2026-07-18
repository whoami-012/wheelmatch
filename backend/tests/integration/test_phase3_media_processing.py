from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, MediaScannerProvider, Settings
from app.core.database import Database, get_session
from app.core.errors import AppError
from app.core.events import EventEnvelope
from app.core.idempotency import IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import OutboxEvent
from app.modules.audit import AuditLog, AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.cursors import ListingCursorCodec
from app.modules.listings.models import Listing
from app.modules.listings.repository import ListingRepository
from app.modules.listings.service import ListingService
from app.modules.media.dependencies import get_media_service
from app.modules.media.models import ListingMedia, MediaDerivative, MediaProcessingEvidence
from app.modules.media.repository import MediaRepository
from app.modules.media.router import router as media_router
from app.modules.media.scanner import DeterministicMalwareScanner
from app.modules.media.service import MediaService
from app.modules.media.storage import MediaStorage
from app.modules.media.worker import MediaProcessingWorker, MediaWorkerResult

pytestmark = pytest.mark.integration


def integration_settings() -> Settings:
    database_url = os.getenv("WHEELMATCH_TEST_DATABASE_URL")
    redis_url = os.getenv("WHEELMATCH_TEST_REDIS_URL")
    aws_endpoint_url = os.getenv("WHEELMATCH_TEST_AWS_ENDPOINT_URL")
    if not database_url or not redis_url or not aws_endpoint_url:
        pytest.skip("PostgreSQL/Redis/LocalStack test endpoints are not configured")
    return Settings(
        _env_file=None,
        environment=Environment.TEST,
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        aws_endpoint_url=aws_endpoint_url,
        s3_media_bucket=os.getenv("WHEELMATCH_TEST_S3_MEDIA_BUCKET", "wheelmatch-media-local"),
        media_scanner_provider=MediaScannerProvider.DETERMINISTIC,
    )


def make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid7(),
        normalized_email=f"phase3-media-{uuid7()}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$not-plaintext",
        status="active",
        email_verified_at=now,
        phone_verified_at=now,
        password_changed_at=now,
    )


def make_jpeg() -> bytes:
    image = Image.new("RGB", (1_200, 800), "navy")
    exif = Image.Exif()
    exif[270] = "must be stripped"
    output = BytesIO()
    image.save(output, format="JPEG", quality=90, exif=exif, comment=b"private")
    image.close()
    return output.getvalue()


def make_png() -> bytes:
    image = Image.new("RGB", (64, 32), "green")
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


async def create_processing_media(
    database: Database,
    storage: MediaStorage,
    *,
    content: bytes,
    content_type: str = "image/jpeg",
    expected_checksum: str | None = None,
    processing_version: int = 1,
) -> tuple[User, User, ListingMedia, EventEnvelope]:
    owner = make_user()
    outsider = make_user()
    listing_id = uuid7()
    media_id = uuid7()
    checksum = expected_checksum or hashlib.sha256(content).hexdigest()
    media = ListingMedia(
        id=media_id,
        listing_id=listing_id,
        created_by_user_id=owner.id,
        object_key=f"quarantine/listings/{listing_id}/{media_id}",
        expected_content_type=content_type,
        expected_size_bytes=len(content),
        expected_checksum_sha256=checksum,
        sort_order=0,
        status="processing",
        processing_version=processing_version,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        completed_at=datetime.now(UTC),
    )
    listing = Listing(
        id=listing_id,
        owner_type="user",
        owner_user_id=owner.id,
        owner_organization_id=None,
        created_by_user_id=owner.id,
        vehicle_type="car",
        lifecycle_status="draft",
        currency="INR",
        version=1,
    )
    async with database.session_factory() as session, session.begin():
        session.add_all([owner, outsider, listing, media])
    upload_url = storage.create_upload_url(
        object_key=media.object_key,
        content_type=content_type,
        size_bytes=len(content),
        checksum_sha256=None,
        expires_seconds=300,
    )
    async with httpx.AsyncClient() as client:
        response = await client.put(
            upload_url,
            content=content,
            headers={"Content-Type": content_type, "Content-Length": str(len(content))},
        )
    assert response.status_code in {200, 204}
    envelope = EventEnvelope(
        event_type="media.processing.requested",
        aggregate_type="listing_media",
        aggregate_id=media.id,
        traceparent="00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        payload={
            "media_id": str(media.id),
            "listing_id": str(media.listing_id),
            "processing_version": processing_version,
        },
    )
    return owner, outsider, media, envelope


def listing_service() -> ListingService:
    return ListingService(
        repository=ListingRepository(),
        identity_repository=IdentityRepository(),
        dealer_repository=DealerRepository(),
        policy=AuthorizationPolicy(),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
        cursor_codec=ListingCursorCodec("phase3-media-integration-cursor-key"),
    )


def processing_worker(
    settings: Settings,
    database: Database,
    storage: MediaStorage,
    *,
    audit: AuditRecorder | None = None,
) -> MediaProcessingWorker:
    return MediaProcessingWorker(
        settings=settings,
        session_factory=database.session_factory,
        repository=MediaRepository(),
        storage=storage,
        scanner=DeterministicMalwareScanner(),
        audit=audit or AuditRecorder(),
    )


@pytest.mark.asyncio
async def test_private_derivatives_duplicate_delivery_status_privacy_and_atomic_evidence() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    storage = MediaStorage(settings)
    try:
        owner, outsider, media, envelope = await create_processing_media(
            database, storage, content=make_jpeg()
        )
        worker = processing_worker(settings, database, storage)

        assert await worker.process(envelope) is MediaWorkerResult.COMPLETED
        assert await worker.process(envelope) is MediaWorkerResult.STALE

        async with database.session_factory() as session:
            persisted = await session.get(ListingMedia, media.id)
            evidence = await session.scalar(
                select(MediaProcessingEvidence).where(
                    MediaProcessingEvidence.media_id == media.id,
                    MediaProcessingEvidence.processing_version == 1,
                )
            )
            derivatives = list(
                await session.scalars(
                    select(MediaDerivative).where(MediaDerivative.media_id == media.id)
                )
            )
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.resource_id == media.id,
                    AuditLog.action == "listing.media.sanitization_moderation_pending",
                )
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == media.id,
                    OutboxEvent.event_type == "media.sanitization.moderation_pending",
                )
            )
        assert persisted is not None and persisted.status == "moderation_pending"
        assert persisted.failure_code is None
        assert evidence is not None and evidence.status == "moderation_pending"
        assert evidence.input_checksum_sha256 and evidence.sanitized_checksum_sha256
        assert evidence.perceptual_hash and evidence.scanner_status == "clean"
        assert len(derivatives) == 3
        assert {row.kind for row in derivatives} == {"thumbnail", "medium", "large"}
        assert all(
            row.object_key.startswith("private/derivatives/listings/") for row in derivatives
        )
        assert audit is not None and event is not None
        assert set(event.payload) == {
            "media_id",
            "listing_id",
            "processing_version",
            "state",
        }

        media_service = MediaService(
            repository=MediaRepository(),
            listing_service=listing_service(),
            storage=storage,
            audit=AuditRecorder(),
            idempotency_repository=IdempotencyRepository(),
        )
        app = FastAPI()
        app.include_router(media_router)

        async def session_override() -> Any:
            async with database.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_media_service] = lambda: media_service
        app.dependency_overrides[get_current_principal] = lambda: SimpleNamespace(user_id=owner.id)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/media/{media.id}/status")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "moderation_pending"
        forbidden = {
            "object_key",
            "upload_url",
            "url",
            "checksum_sha256",
            "perceptual_hash",
            "metadata",
            "scanner_status",
        }
        assert forbidden.isdisjoint(body)

        async with database.session_factory() as session:
            with pytest.raises(AppError) as denied:
                await media_service.status(session, actor_user_id=outsider.id, media_id=media.id)
        assert denied.value.status == 404
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_stale_processing_version_cannot_claim_or_overwrite_current_state() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    storage = MediaStorage(settings)
    try:
        _, _, media, current = await create_processing_media(
            database, storage, content=make_jpeg(), processing_version=2
        )
        stale = current.model_copy(
            update={
                "payload": {**current.payload, "processing_version": 1},
            }
        )

        result = await processing_worker(settings, database, storage).process(stale)

        assert result is MediaWorkerResult.STALE
        async with database.session_factory() as session:
            persisted = await session.get(ListingMedia, media.id)
            evidence_count = await session.scalar(
                select(func.count())
                .select_from(MediaProcessingEvidence)
                .where(MediaProcessingEvidence.media_id == media.id)
            )
        assert persisted is not None and persisted.status == "processing"
        assert persisted.processing_version == 2
        assert evidence_count == 0
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "content_type", "expected_checksum", "failure_code"),
    [
        pytest.param(
            b"not-an-image",
            "image/jpeg",
            None,
            "UNSUPPORTED_FILE_SIGNATURE",
            id="invalid-signature",
        ),
        pytest.param(
            make_png(),
            "image/jpeg",
            None,
            "OBJECT_MIME_MISMATCH",
            id="mime-mismatch",
        ),
        pytest.param(
            make_jpeg(),
            "image/jpeg",
            "0" * 64,
            "OBJECT_CHECKSUM_MISMATCH",
            id="checksum-mismatch",
        ),
    ],
)
async def test_invalid_signature_mime_or_checksum_has_safe_terminal_result(
    content: bytes,
    content_type: str,
    expected_checksum: str | None,
    failure_code: str,
) -> None:
    settings = integration_settings()
    database = Database.create(settings)
    storage = MediaStorage(settings)
    try:
        _, _, media, envelope = await create_processing_media(
            database,
            storage,
            content=content,
            content_type=content_type,
            expected_checksum=expected_checksum,
        )

        result = await processing_worker(settings, database, storage).process(envelope)

        assert result is MediaWorkerResult.COMPLETED
        async with database.session_factory() as session:
            persisted = await session.get(ListingMedia, media.id)
            evidence = await session.scalar(
                select(MediaProcessingEvidence).where(MediaProcessingEvidence.media_id == media.id)
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == media.id,
                    OutboxEvent.event_type == "media.sanitization.rejected",
                )
            )
        assert persisted is not None and persisted.status == "rejected"
        assert persisted.failure_code == failure_code
        assert evidence is not None and evidence.failure_code == failure_code
        assert event is not None and event.payload["failure_code"] == failure_code
        assert set(event.payload) == {
            "media_id",
            "listing_id",
            "processing_version",
            "state",
            "failure_code",
        }
    finally:
        await database.close()


class FailingAuditRecorder(AuditRecorder):
    def record(
        self,
        session: AsyncSession,
        *,
        action: str,
        outcome: str,
        resource_type: str,
        actor_user_id: UUID | None = None,
        resource_id: UUID | None = None,
        organization_id: UUID | None = None,
        membership_id: UUID | None = None,
        reason_code: str | None = None,
        changes: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AuditLog:
        del (
            session,
            action,
            outcome,
            resource_type,
            actor_user_id,
            resource_id,
            organization_id,
            membership_id,
            reason_code,
            changes,
            request_id,
            trace_id,
        )
        raise RuntimeError("forced atomicity failure")


@pytest.mark.asyncio
async def test_final_media_state_evidence_audit_and_outbox_commit_atomically() -> None:
    settings = integration_settings()
    database = Database.create(settings)
    storage = MediaStorage(settings)
    try:
        _, _, media, envelope = await create_processing_media(
            database, storage, content=make_jpeg()
        )
        worker = processing_worker(
            settings, database, storage, audit=cast(AuditRecorder, FailingAuditRecorder())
        )

        with pytest.raises(RuntimeError, match="forced atomicity failure"):
            await worker.process(envelope)

        async with database.session_factory() as session:
            persisted = await session.get(ListingMedia, media.id)
            evidence = await session.scalar(
                select(MediaProcessingEvidence).where(MediaProcessingEvidence.media_id == media.id)
            )
            derivative_count = await session.scalar(
                select(func.count())
                .select_from(MediaDerivative)
                .where(MediaDerivative.media_id == media.id)
            )
            terminal_audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.resource_id == media.id,
                    AuditLog.action == "listing.media.sanitization_moderation_pending",
                )
            )
            terminal_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == media.id,
                    OutboxEvent.event_type == "media.sanitization.moderation_pending",
                )
            )
        assert persisted is not None and persisted.status == "scanning"
        assert evidence is not None and evidence.status == "processing"
        assert derivative_count == 0
        assert terminal_audit is None
        assert terminal_event is None
    finally:
        await database.close()
