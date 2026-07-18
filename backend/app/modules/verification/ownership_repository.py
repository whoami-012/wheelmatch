from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.catalogue.models import CanonicalVehicle
from app.modules.verification.ownership_models import VehicleOwnershipVerification


class OwnershipVerificationRepository:
    async def lock_canonical_identity(
        self, session: AsyncSession, *, registration_hmac: str
    ) -> None:
        """Serialize equivalent canonical resolution without exposing raw identity."""
        lock_key = int(registration_hmac[:16], 16) & ((1 << 63) - 1)
        await session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def find_canonical(
        self,
        session: AsyncSession,
        *,
        jurisdiction: str,
        hash_version: int,
        registration_hmac: str,
        vin_hmac: str | None,
        chassis_hmac: str | None,
    ) -> list[CanonicalVehicle]:
        predicates: list[ColumnElement[bool]] = [
            (
                (CanonicalVehicle.jurisdiction == jurisdiction)
                & (CanonicalVehicle.registration_hmac == registration_hmac)
            )
        ]
        if vin_hmac is not None:
            predicates.append(CanonicalVehicle.vin_hmac == vin_hmac)
        if chassis_hmac is not None:
            predicates.append(CanonicalVehicle.chassis_hmac == chassis_hmac)
        result = await session.scalars(
            select(CanonicalVehicle)
            .where(CanonicalVehicle.hash_version == hash_version, or_(*predicates))
            .with_for_update()
        )
        return list(result)

    async def get_canonical(
        self, session: AsyncSession, canonical_vehicle_id: UUID, *, for_update: bool = False
    ) -> CanonicalVehicle | None:
        statement: Select[tuple[CanonicalVehicle]] = select(CanonicalVehicle).where(
            CanonicalVehicle.id == canonical_vehicle_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(CanonicalVehicle | None, await session.scalar(statement))

    async def get_attempt(
        self, session: AsyncSession, attempt_id: UUID, *, for_update: bool = False
    ) -> VehicleOwnershipVerification | None:
        statement: Select[tuple[VehicleOwnershipVerification]] = select(
            VehicleOwnershipVerification
        ).where(VehicleOwnershipVerification.id == attempt_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(VehicleOwnershipVerification | None, await session.scalar(statement))

    async def get_by_provider_reference(
        self,
        session: AsyncSession,
        *,
        provider_identifier: str,
        provider_reference: str,
        for_update: bool = False,
    ) -> VehicleOwnershipVerification | None:
        statement: Select[tuple[VehicleOwnershipVerification]] = select(
            VehicleOwnershipVerification
        ).where(
            VehicleOwnershipVerification.provider_identifier == provider_identifier,
            VehicleOwnershipVerification.provider_reference == provider_reference,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(VehicleOwnershipVerification | None, await session.scalar(statement))

    async def get_by_result_event(
        self, session: AsyncSession, *, provider_identifier: str, event_id: str
    ) -> VehicleOwnershipVerification | None:
        return cast(
            VehicleOwnershipVerification | None,
            await session.scalar(
                select(VehicleOwnershipVerification).where(
                    VehicleOwnershipVerification.provider_identifier == provider_identifier,
                    VehicleOwnershipVerification.provider_result_event_id == event_id,
                )
            ),
        )

    async def get_active(
        self, session: AsyncSession, *, owner_user_id: UUID, canonical_vehicle_id: UUID
    ) -> VehicleOwnershipVerification | None:
        return cast(
            VehicleOwnershipVerification | None,
            await session.scalar(
                select(VehicleOwnershipVerification)
                .where(
                    VehicleOwnershipVerification.owner_user_id == owner_user_id,
                    VehicleOwnershipVerification.canonical_vehicle_id == canonical_vehicle_id,
                    VehicleOwnershipVerification.status.in_(
                        ("session_pending", "pending", "manual_review")
                    ),
                    VehicleOwnershipVerification.superseded_at.is_(None),
                )
                .order_by(VehicleOwnershipVerification.attempt_number.desc())
                .limit(1)
            ),
        )

    async def get_latest(
        self,
        session: AsyncSession,
        *,
        owner_user_id: UUID,
        canonical_vehicle_id: UUID,
        for_update: bool = False,
    ) -> VehicleOwnershipVerification | None:
        statement: Select[tuple[VehicleOwnershipVerification]] = (
            select(VehicleOwnershipVerification)
            .where(
                VehicleOwnershipVerification.owner_user_id == owner_user_id,
                VehicleOwnershipVerification.canonical_vehicle_id == canonical_vehicle_id,
            )
            .order_by(VehicleOwnershipVerification.attempt_number.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(VehicleOwnershipVerification | None, await session.scalar(statement))

    async def list_for_reuse(
        self,
        session: AsyncSession,
        *,
        owner_user_id: UUID,
        canonical_vehicle_id: UUID,
        for_update: bool = False,
    ) -> list[VehicleOwnershipVerification]:
        statement: Select[tuple[VehicleOwnershipVerification]] = (
            select(VehicleOwnershipVerification)
            .where(
                VehicleOwnershipVerification.owner_user_id == owner_user_id,
                VehicleOwnershipVerification.canonical_vehicle_id == canonical_vehicle_id,
            )
            .order_by(VehicleOwnershipVerification.attempt_number.desc())
        )
        if for_update:
            statement = statement.with_for_update()
        return list(await session.scalars(statement))

    async def next_attempt_number(
        self, session: AsyncSession, *, owner_user_id: UUID, canonical_vehicle_id: UUID
    ) -> int:
        value = await session.scalar(
            select(func.max(VehicleOwnershipVerification.attempt_number)).where(
                VehicleOwnershipVerification.owner_user_id == owner_user_id,
                VehicleOwnershipVerification.canonical_vehicle_id == canonical_vehicle_id,
            )
        )
        return int(value or 0) + 1
