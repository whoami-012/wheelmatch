from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import IdempotencyConflictError, IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.listings.service import ListingService
from app.modules.media.models import ListingMedia
from app.modules.media.repository import MediaRepository
from app.modules.media.schemas import (
    MediaCompleteRequest,
    MediaStatusResponse,
    MediaUploadIntentRequest,
    MediaUploadIntentResponse,
)
from app.modules.media.storage import MediaStorage


class MediaService:
    def __init__(
        self,
        *,
        repository: MediaRepository,
        listing_service: ListingService,
        storage: MediaStorage,
        audit: AuditRecorder,
        idempotency_repository: IdempotencyRepository,
        max_count: int = 20,
        intent_ttl_seconds: int = 900,
    ) -> None:
        self._repository = repository
        self._listings = listing_service
        self._storage = storage
        self._audit = audit
        self._idempotency = idempotency_repository
        self._max_count = max_count
        self._intent_ttl = intent_ttl_seconds

    async def create_intent(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        request: MediaUploadIntentRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> MediaUploadIntentResponse:
        now = datetime.now(UTC)
        media: ListingMedia | None = None
        async with session.begin():
            try:
                reservation = await self._idempotency.reserve(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation="media.upload_intent.create",
                    key=idempotency_key,
                    request_hash=request_hash,
                    expires_at=now + timedelta(hours=24),
                )
            except IdempotencyConflictError as exc:
                raise AppError(
                    status=409,
                    code="IDEMPOTENCY_KEY_CONFLICT",
                    title="Idempotency key conflicts with an earlier request",
                ) from exc
            if not reservation.acquired:
                if reservation.replay_body is None:
                    raise AppError(
                        status=409,
                        code="IDEMPOTENCY_IN_PROGRESS",
                        title="An idempotent request is already in progress",
                    )
                media = await self._repository.get(
                    session, UUID(str(reservation.replay_body["media_id"])), for_update=True
                )
                if media is None or media.status != "intent_created" or media.expires_at <= now:
                    raise AppError(
                        status=409, code="MEDIA_INTENT_EXPIRED", title="Media upload intent expired"
                    )
            else:
                listing, actor, membership_id = await self._listings.get_authorized_listing(
                    session,
                    actor_user_id=actor_user_id,
                    listing_id=request.listing_id,
                    for_update=True,
                )
                if listing.lifecycle_status != "draft":
                    raise AppError(
                        status=409, code="LISTING_NOT_EDITABLE", title="Listing is not editable"
                    )
                if await self._repository.count_active(session, listing.id) >= self._max_count:
                    raise AppError(
                        status=422, code="MEDIA_LIMIT_REACHED", title="Listing media limit reached"
                    )
                media_id = uuid7()
                media = ListingMedia(
                    id=media_id,
                    listing_id=listing.id,
                    created_by_user_id=actor.id,
                    object_key=f"quarantine/listings/{listing.id}/{media_id}",
                    expected_content_type=request.content_type,
                    expected_size_bytes=request.size_bytes,
                    expected_checksum_sha256=request.checksum_sha256.casefold()
                    if request.checksum_sha256
                    else None,
                    sort_order=request.sort_order,
                    status="intent_created",
                    expires_at=now + timedelta(seconds=self._intent_ttl),
                )
                session.add(media)
                listing.version += 1
                self._audit.record(
                    session,
                    action="listing.media.intent_created",
                    outcome="success",
                    resource_type="listing_media",
                    actor_user_id=actor.id,
                    organization_id=listing.owner_organization_id,
                    membership_id=membership_id,
                    resource_id=media.id,
                    changes={
                        "content_type": request.content_type,
                        "sort_order": request.sort_order,
                    },
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="listing.media.intent_created",
                    aggregate_type="listing",
                    aggregate_id=listing.id,
                    payload={"media_id": str(media.id), "listing_version": listing.version},
                )
                try:
                    await session.flush()
                except IntegrityError as exc:
                    raise AppError(
                        status=409,
                        code="MEDIA_ORDER_CONFLICT",
                        title="Media order is already in use",
                    ) from exc
                await self._idempotency.complete(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation="media.upload_intent.create",
                    key=idempotency_key,
                    response_status=201,
                    response_body={"media_id": str(media.id)},
                    resource_type="listing_media",
                    resource_id=media.id,
                )
        if media is None:
            raise AssertionError("media intent completed without a record")
        remaining = max(1, int((media.expires_at - datetime.now(UTC)).total_seconds()))
        upload_url = self._storage.create_upload_url(
            object_key=media.object_key,
            content_type=media.expected_content_type,
            size_bytes=media.expected_size_bytes,
            checksum_sha256=media.expected_checksum_sha256,
            expires_seconds=min(self._intent_ttl, remaining),
        )
        headers = {
            "Content-Type": media.expected_content_type,
            "Content-Length": str(media.expected_size_bytes),
        }
        if media.expected_checksum_sha256 is not None:
            headers["x-amz-checksum-sha256"] = base64.b64encode(
                bytes.fromhex(media.expected_checksum_sha256)
            ).decode()
        return MediaUploadIntentResponse(
            media_id=media.id,
            upload_url=upload_url,
            required_headers=headers,
            expires_at=media.expires_at,
        )

    async def complete(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        media_id: UUID,
        request: MediaCompleteRequest,
    ) -> MediaStatusResponse:
        candidate = await self._repository.get(session, media_id)
        if candidate is None:
            raise self._not_found()
        object_key = candidate.object_key
        listing_id = candidate.listing_id
        await self._listings.get_authorized_listing(
            session, actor_user_id=actor_user_id, listing_id=listing_id
        )
        await session.rollback()
        metadata = await self._storage.head(object_key)
        async with session.begin():
            media = await self._repository.get(session, media_id, for_update=True)
            if media is None:
                raise self._not_found()
            listing, actor, membership_id = await self._listings.get_authorized_listing(
                session,
                actor_user_id=actor_user_id,
                listing_id=media.listing_id,
                for_update=True,
            )
            now = datetime.now(UTC)
            if media.status != "intent_created" or media.expires_at <= now:
                raise AppError(
                    status=409, code="MEDIA_INTENT_EXPIRED", title="Media upload intent expired"
                )
            expected_checksum = media.expected_checksum_sha256
            supplied_checksum = (
                request.checksum_sha256.casefold() if request.checksum_sha256 else None
            )
            if (
                metadata.size_bytes != media.expected_size_bytes
                or request.size_bytes != media.expected_size_bytes
                or metadata.content_type != media.expected_content_type
                or (expected_checksum is not None and supplied_checksum != expected_checksum)
            ):
                raise AppError(
                    status=422,
                    code="MEDIA_OBJECT_MISMATCH",
                    title="Uploaded object does not match the intent",
                )
            media.status = "processing"
            media.completed_at = now
            listing.version += 1
            self._audit.record(
                session,
                action="listing.media.completed",
                outcome="success",
                resource_type="listing_media",
                actor_user_id=actor.id,
                organization_id=listing.owner_organization_id,
                membership_id=membership_id,
                resource_id=media.id,
                changes={"status": "processing", "processing_version": media.processing_version},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="media.processing.requested",
                aggregate_type="listing_media",
                aggregate_id=media.id,
                payload={
                    "media_id": str(media.id),
                    "listing_id": str(media.listing_id),
                    "processing_version": media.processing_version,
                },
            )
            await session.flush()
            return self._response(media)

    async def status(
        self, session: AsyncSession, *, actor_user_id: UUID, media_id: UUID
    ) -> MediaStatusResponse:
        media = await self._repository.get(session, media_id)
        if media is None:
            raise self._not_found()
        await self._listings.get_authorized_listing(
            session, actor_user_id=actor_user_id, listing_id=media.listing_id
        )
        return self._response(media)

    async def remove(self, session: AsyncSession, *, actor_user_id: UUID, media_id: UUID) -> None:
        async with session.begin():
            media = await self._repository.get(session, media_id, for_update=True)
            if media is None:
                raise self._not_found()
            listing, actor, membership_id = await self._listings.get_authorized_listing(
                session,
                actor_user_id=actor_user_id,
                listing_id=media.listing_id,
                for_update=True,
            )
            if media.status == "removed":
                return
            media.status = "removed"
            media.removed_at = datetime.now(UTC)
            listing.version += 1
            self._audit.record(
                session,
                action="listing.media.removed",
                outcome="success",
                resource_type="listing_media",
                actor_user_id=actor.id,
                organization_id=listing.owner_organization_id,
                membership_id=membership_id,
                resource_id=media.id,
                changes={"status": "removed"},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="media.removal.requested",
                aggregate_type="listing_media",
                aggregate_id=media.id,
                payload={"media_id": str(media.id), "listing_id": str(media.listing_id)},
            )

    @staticmethod
    def _response(media: ListingMedia) -> MediaStatusResponse:
        return MediaStatusResponse(
            media_id=media.id,
            listing_id=media.listing_id,
            status=media.status,
            content_type=media.expected_content_type,
            size_bytes=media.expected_size_bytes,
            sort_order=media.sort_order,
            expires_at=media.expires_at,
            processing_version=media.processing_version,
            failure_code=media.failure_code,
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(status=404, code="MEDIA_NOT_FOUND", title="Media not found")
