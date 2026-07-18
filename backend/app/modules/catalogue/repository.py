from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalogue.models import VehicleMake, VehicleModel, VehicleVariant
from app.modules.catalogue.schemas import CatalogueSearchItem


class CatalogueRepository:
    async def list_makes(
        self, session: AsyncSession, *, vehicle_type: str, normalized_query: str | None, limit: int
    ) -> list[VehicleMake]:
        statement = select(VehicleMake).where(
            or_(VehicleMake.vehicle_type == vehicle_type, VehicleMake.vehicle_type == "both")
        )
        if normalized_query:
            statement = statement.where(VehicleMake.normalized_name.startswith(normalized_query))
        result = await session.scalars(
            statement.order_by(VehicleMake.normalized_name, VehicleMake.id).limit(limit)
        )
        return list(result)

    async def list_models(
        self, session: AsyncSession, *, make_id: UUID, vehicle_type: str, limit: int
    ) -> list[VehicleModel]:
        result = await session.scalars(
            select(VehicleModel)
            .where(VehicleModel.make_id == make_id, VehicleModel.vehicle_type == vehicle_type)
            .order_by(VehicleModel.normalized_name, VehicleModel.id)
            .limit(limit)
        )
        return list(result)

    async def list_variants(
        self, session: AsyncSession, *, model_id: UUID, limit: int
    ) -> list[VehicleVariant]:
        result = await session.scalars(
            select(VehicleVariant)
            .where(VehicleVariant.model_id == model_id)
            .order_by(VehicleVariant.normalized_name, VehicleVariant.id)
            .limit(limit)
        )
        return list(result)

    async def search(
        self, session: AsyncSession, *, vehicle_type: str, normalized_query: str, limit: int
    ) -> list[CatalogueSearchItem]:
        rows = await session.execute(
            select(VehicleVariant, VehicleModel, VehicleMake)
            .join(VehicleModel, VehicleModel.id == VehicleVariant.model_id)
            .join(VehicleMake, VehicleMake.id == VehicleModel.make_id)
            .where(
                VehicleModel.vehicle_type == vehicle_type,
                or_(
                    VehicleMake.normalized_name.contains(normalized_query),
                    VehicleModel.normalized_name.contains(normalized_query),
                    VehicleVariant.normalized_name.contains(normalized_query),
                ),
            )
            .order_by(
                VehicleMake.normalized_name,
                VehicleModel.normalized_name,
                VehicleVariant.normalized_name,
            )
            .limit(limit)
        )
        return [
            CatalogueSearchItem(
                variant_id=variant.id,
                variant_name=variant.name,
                model_id=model.id,
                model_name=model.name,
                make_id=make.id,
                make_name=make.name,
                vehicle_type=model.vehicle_type,
            )
            for variant, model, make in rows.all()
        ]
