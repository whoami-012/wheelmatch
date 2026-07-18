from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.locations.models import DealerPublicAddress, ListingLocation


class LocationRepository:
    async def upsert(
        self,
        session: AsyncSession,
        *,
        listing_id: UUID,
        latitude: float | None,
        longitude: float | None,
        locality: str,
        coarse_area: str,
        visibility: str,
        public_address_id: UUID | None,
    ) -> None:
        point: ColumnElement[Any]
        if public_address_id is None:
            if latitude is None or longitude is None:
                raise ValueError("private coordinates are required")
            point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        else:
            point = (
                select(DealerPublicAddress.exact_point)
                .where(DealerPublicAddress.id == public_address_id)
                .scalar_subquery()
            )
        statement = insert(ListingLocation).values(
            listing_id=listing_id,
            exact_point=point,
            locality=locality,
            coarse_area=coarse_area,
            visibility=visibility,
            public_address_id=public_address_id,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["listing_id"],
            set_={
                "exact_point": point,
                "locality": locality,
                "coarse_area": coarse_area,
                "visibility": visibility,
                "public_address_id": public_address_id,
                "updated_at": func.now(),
            },
        )
        await session.execute(statement)

    async def get(self, session: AsyncSession, listing_id: UUID) -> ListingLocation | None:
        return cast(ListingLocation | None, await session.get(ListingLocation, listing_id))

    async def get_public_address(
        self, session: AsyncSession, address_id: UUID
    ) -> DealerPublicAddress | None:
        return cast(DealerPublicAddress | None, await session.get(DealerPublicAddress, address_id))

    async def find_listing_ids_within(
        self,
        session: AsyncSession,
        *,
        latitude: float,
        longitude: float,
        radius_meters: int,
        limit: int,
    ) -> list[UUID]:
        origin = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        result = await session.scalars(
            select(ListingLocation.listing_id)
            .where(func.ST_DWithin(ListingLocation.exact_point, origin, radius_meters))
            .order_by(ListingLocation.listing_id)
            .limit(limit)
        )
        return list(result)
