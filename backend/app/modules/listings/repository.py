from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalogue.models import VehicleModel, VehicleVariant
from app.modules.listings.models import BikeSpec, CarSpec, Listing, VehicleSpec


class ListingRepository:
    async def get(
        self, session: AsyncSession, listing_id: UUID, *, for_update: bool = False
    ) -> Listing | None:
        statement: Select[tuple[Listing]] = select(Listing).where(Listing.id == listing_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(Listing | None, await session.scalar(statement))

    async def variant_matches_type(
        self, session: AsyncSession, *, variant_id: UUID, vehicle_type: str
    ) -> bool:
        return (
            await session.scalar(
                select(VehicleVariant.id)
                .join(VehicleModel, VehicleModel.id == VehicleVariant.model_id)
                .where(VehicleVariant.id == variant_id, VehicleModel.vehicle_type == vehicle_type)
            )
            is not None
        )

    async def get_specs(
        self, session: AsyncSession, listing_id: UUID
    ) -> tuple[VehicleSpec | None, CarSpec | None, BikeSpec | None]:
        return (
            await session.get(VehicleSpec, listing_id),
            await session.get(CarSpec, listing_id),
            await session.get(BikeSpec, listing_id),
        )

    async def replace_specs(
        self,
        session: AsyncSession,
        *,
        listing: Listing,
        vehicle_spec: VehicleSpec | None,
        car_spec: CarSpec | None,
        bike_spec: BikeSpec | None,
    ) -> None:
        if vehicle_spec is not None:
            await session.merge(vehicle_spec)
        if car_spec is not None:
            await session.execute(delete(BikeSpec).where(BikeSpec.listing_id == listing.id))
            await session.merge(car_spec)
        if bike_spec is not None:
            await session.execute(delete(CarSpec).where(CarSpec.listing_id == listing.id))
            await session.merge(bike_spec)

    async def list_owned(
        self,
        session: AsyncSession,
        *,
        owner_user_id: UUID | None,
        owner_organization_id: UUID | None,
        lifecycle_status: str,
        before_updated_at: datetime | None,
        before_id: UUID | None,
        limit: int,
    ) -> list[Listing]:
        statement = select(Listing).where(Listing.lifecycle_status == lifecycle_status)
        if owner_user_id is not None:
            statement = statement.where(
                Listing.owner_user_id == owner_user_id,
                Listing.owner_organization_id.is_(None),
            )
        else:
            statement = statement.where(
                Listing.owner_user_id.is_(None),
                Listing.owner_organization_id == owner_organization_id,
            )
        if before_updated_at is not None and before_id is not None:
            statement = statement.where(
                (Listing.updated_at < before_updated_at)
                | ((Listing.updated_at == before_updated_at) & (Listing.id < before_id))
            )
        result = await session.scalars(
            statement.order_by(Listing.updated_at.desc(), Listing.id.desc()).limit(limit)
        )
        return list(result)
