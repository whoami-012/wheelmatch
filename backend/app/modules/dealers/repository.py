from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealers.models import DealerMembership, DealerOrganization


class DealerRepository:
    async def get_organization(
        self, session: AsyncSession, organization_id: UUID, *, for_update: bool = False
    ) -> DealerOrganization | None:
        statement: Select[tuple[DealerOrganization]] = select(DealerOrganization).where(
            DealerOrganization.id == organization_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(DealerOrganization | None, await session.scalar(statement))

    async def get_membership(
        self, session: AsyncSession, membership_id: UUID, *, for_update: bool = False
    ) -> DealerMembership | None:
        statement: Select[tuple[DealerMembership]] = select(DealerMembership).where(
            DealerMembership.id == membership_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(DealerMembership | None, await session.scalar(statement))

    async def get_membership_for_user(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> DealerMembership | None:
        statement: Select[tuple[DealerMembership]] = select(DealerMembership).where(
            DealerMembership.organization_id == organization_id,
            DealerMembership.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(DealerMembership | None, await session.scalar(statement))

    async def list_user_memberships(
        self, session: AsyncSession, user_id: UUID
    ) -> list[tuple[DealerMembership, DealerOrganization]]:
        rows = await session.execute(
            select(DealerMembership, DealerOrganization)
            .join(
                DealerOrganization,
                DealerOrganization.id == DealerMembership.organization_id,
            )
            .where(DealerMembership.user_id == user_id)
            .order_by(DealerMembership.created_at.desc(), DealerMembership.id.desc())
        )
        return [(row[0], row[1]) for row in rows.all()]

    async def list_active_organization_user_ids(
        self, session: AsyncSession, organization_id: UUID
    ) -> list[UUID]:
        result = await session.scalars(
            select(DealerMembership.user_id).where(
                DealerMembership.organization_id == organization_id,
                DealerMembership.status == "active",
            )
        )
        return list(result)

    async def count_other_active_owners(
        self, session: AsyncSession, *, organization_id: UUID, excluded_membership_id: UUID
    ) -> int:
        value = await session.scalar(
            select(func.count())
            .select_from(DealerMembership)
            .where(
                DealerMembership.organization_id == organization_id,
                DealerMembership.id != excluded_membership_id,
                DealerMembership.role == "owner",
                DealerMembership.status == "active",
            )
        )
        return int(value or 0)
