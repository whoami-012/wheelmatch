from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.listings.submission_models import ListingSubmissionAttempt


class ListingSubmissionRepository:
    async def get_for_version(
        self,
        session: AsyncSession,
        *,
        listing_id: UUID,
        listing_version: int,
        for_update: bool = False,
    ) -> ListingSubmissionAttempt | None:
        statement: Select[tuple[ListingSubmissionAttempt]] = select(ListingSubmissionAttempt).where(
            ListingSubmissionAttempt.listing_id == listing_id,
            ListingSubmissionAttempt.listing_version == listing_version,
            ListingSubmissionAttempt.superseded_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(ListingSubmissionAttempt | None, await session.scalar(statement))

    async def get_latest(
        self, session: AsyncSession, *, listing_id: UUID
    ) -> ListingSubmissionAttempt | None:
        return cast(
            ListingSubmissionAttempt | None,
            await session.scalar(
                select(ListingSubmissionAttempt)
                .where(ListingSubmissionAttempt.listing_id == listing_id)
                .order_by(ListingSubmissionAttempt.attempt_number.desc())
                .limit(1)
            ),
        )

    async def next_attempt_number(self, session: AsyncSession, *, listing_id: UUID) -> int:
        value = await session.scalar(
            select(func.max(ListingSubmissionAttempt.attempt_number)).where(
                ListingSubmissionAttempt.listing_id == listing_id
            )
        )
        return int(value or 0) + 1

    async def supersede_other_versions(
        self,
        session: AsyncSession,
        *,
        listing_id: UUID,
        current_version: int,
        superseded_at: datetime,
    ) -> None:
        await session.execute(
            update(ListingSubmissionAttempt)
            .where(
                ListingSubmissionAttempt.listing_id == listing_id,
                ListingSubmissionAttempt.listing_version != current_version,
                ListingSubmissionAttempt.superseded_at.is_(None),
            )
            .values(superseded_at=superseded_at)
        )
