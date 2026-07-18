from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User
from app.modules.profiles.models import Profile, SellerProfile


class ProfileRepository:
    async def get_profile(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> Profile | None:
        statement: Select[tuple[Profile]] = select(Profile).where(Profile.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(Profile | None, await session.scalar(statement))

    async def get_seller_profile(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> SellerProfile | None:
        statement: Select[tuple[SellerProfile]] = select(SellerProfile).where(
            SellerProfile.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(SellerProfile | None, await session.scalar(statement))

    async def get_user_and_profile(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> tuple[User, Profile] | None:
        statement = (
            select(User, Profile)
            .join(Profile, Profile.user_id == User.id)
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).one_or_none()
        return (row[0], row[1]) if row is not None else None
