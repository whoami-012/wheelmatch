from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.media.models import ListingMedia, MediaDerivative, MediaProcessingEvidence


class MediaRepository:
    async def get(
        self, session: AsyncSession, media_id: UUID, *, for_update: bool = False
    ) -> ListingMedia | None:
        statement: Select[tuple[ListingMedia]] = select(ListingMedia).where(
            ListingMedia.id == media_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(ListingMedia | None, await session.scalar(statement))

    async def count_active(self, session: AsyncSession, listing_id: UUID) -> int:
        value = await session.scalar(
            select(func.count())
            .select_from(ListingMedia)
            .where(ListingMedia.listing_id == listing_id, ListingMedia.status != "removed")
        )
        return int(value or 0)

    async def list_for_readiness(
        self, session: AsyncSession, listing_id: UUID, *, for_update: bool = False
    ) -> list[ListingMedia]:
        statement: Select[tuple[ListingMedia]] = (
            select(ListingMedia)
            .where(ListingMedia.listing_id == listing_id, ListingMedia.status != "removed")
            .order_by(ListingMedia.sort_order, ListingMedia.id)
        )
        if for_update:
            statement = statement.with_for_update()
        return list(await session.scalars(statement))

    async def get_evidence(
        self,
        session: AsyncSession,
        media_id: UUID,
        processing_version: int,
        *,
        for_update: bool = False,
    ) -> MediaProcessingEvidence | None:
        statement: Select[tuple[MediaProcessingEvidence]] = select(MediaProcessingEvidence).where(
            MediaProcessingEvidence.media_id == media_id,
            MediaProcessingEvidence.processing_version == processing_version,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(MediaProcessingEvidence | None, await session.scalar(statement))

    async def derivatives_for_version(
        self, session: AsyncSession, media_id: UUID, processing_version: int
    ) -> list[MediaDerivative]:
        result = await session.scalars(
            select(MediaDerivative)
            .where(
                MediaDerivative.media_id == media_id,
                MediaDerivative.processing_version == processing_version,
            )
            .order_by(MediaDerivative.kind, MediaDerivative.id)
        )
        return list(result)
