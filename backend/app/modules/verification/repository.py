from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.verification.models import IdentityVerification, UserVerificationState


class VerificationRepository:
    async def get_attempt(
        self, session: AsyncSession, attempt_id: UUID, *, for_update: bool = False
    ) -> IdentityVerification | None:
        statement: Select[tuple[IdentityVerification]] = select(IdentityVerification).where(
            IdentityVerification.id == attempt_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(IdentityVerification | None, await session.scalar(statement))

    async def get_by_provider_reference(
        self,
        session: AsyncSession,
        *,
        provider_identifier: str,
        provider_reference: str,
        for_update: bool = False,
    ) -> IdentityVerification | None:
        statement: Select[tuple[IdentityVerification]] = select(IdentityVerification).where(
            IdentityVerification.provider_identifier == provider_identifier,
            IdentityVerification.provider_reference == provider_reference,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(IdentityVerification | None, await session.scalar(statement))

    async def get_by_result_event(
        self, session: AsyncSession, *, provider_identifier: str, event_id: str
    ) -> IdentityVerification | None:
        return cast(
            IdentityVerification | None,
            await session.scalar(
                select(IdentityVerification).where(
                    IdentityVerification.provider_identifier == provider_identifier,
                    IdentityVerification.provider_result_event_id == event_id,
                )
            ),
        )

    async def get_active_for_user(
        self, session: AsyncSession, user_id: UUID
    ) -> IdentityVerification | None:
        return cast(
            IdentityVerification | None,
            await session.scalar(
                select(IdentityVerification)
                .where(
                    IdentityVerification.user_id == user_id,
                    IdentityVerification.status.in_(
                        ("session_pending", "pending", "manual_review")
                    ),
                )
                .order_by(IdentityVerification.attempt_number.desc())
                .limit(1)
            ),
        )

    async def get_latest_for_user(
        self, session: AsyncSession, user_id: UUID
    ) -> IdentityVerification | None:
        return cast(
            IdentityVerification | None,
            await session.scalar(
                select(IdentityVerification)
                .where(IdentityVerification.user_id == user_id)
                .order_by(IdentityVerification.attempt_number.desc())
                .limit(1)
            ),
        )

    async def next_attempt_number(self, session: AsyncSession, user_id: UUID) -> int:
        value = await session.scalar(
            select(func.max(IdentityVerification.attempt_number)).where(
                IdentityVerification.user_id == user_id
            )
        )
        return int(value or 0) + 1

    async def get_state(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> UserVerificationState | None:
        statement: Select[tuple[UserVerificationState]] = select(UserVerificationState).where(
            UserVerificationState.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(UserVerificationState | None, await session.scalar(statement))
