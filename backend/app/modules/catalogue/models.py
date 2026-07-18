from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base
from app.core.ids import uuid7


class VehicleMake(Base):
    __tablename__ = "vehicle_makes"
    __table_args__ = (
        CheckConstraint("vehicle_type IN ('car', 'bike', 'both')", name="vehicle_type_valid"),
        UniqueConstraint("normalized_name", name="uq_vehicle_makes_normalized_name"),
        Index("ix_vehicle_makes_type_name", "vehicle_type", "normalized_name", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VehicleModel(Base):
    __tablename__ = "vehicle_models"
    __table_args__ = (
        CheckConstraint("vehicle_type IN ('car', 'bike')", name="vehicle_type_valid"),
        UniqueConstraint(
            "make_id", "vehicle_type", "normalized_name", name="uq_vehicle_models_parent_name"
        ),
        Index(
            "ix_vehicle_models_make_type_name", "make_id", "vehicle_type", "normalized_name", "id"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    make_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicle_makes.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VehicleVariant(Base):
    __tablename__ = "vehicle_variants"
    __table_args__ = (
        UniqueConstraint("model_id", "normalized_name", name="uq_vehicle_variants_parent_name"),
        Index("ix_vehicle_variants_model_name", "model_id", "normalized_name", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    model_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicle_models.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CanonicalVehicle(Base):
    __tablename__ = "canonical_vehicles"
    __table_args__ = (
        CheckConstraint("vehicle_type IN ('car', 'bike')", name="vehicle_type_valid"),
        CheckConstraint("hash_version > 0", name="hash_version_positive"),
        CheckConstraint("identity_version > 0", name="identity_version_positive"),
        CheckConstraint(
            "identity_status IN ('active', 'disputed', 'transferred', 'stolen', "
            "'written_off', 'fraud_review')",
            name="identity_status_valid",
        ),
        CheckConstraint(
            "registration_hmac IS NOT NULL OR vin_hmac IS NOT NULL OR chassis_hmac IS NOT NULL",
            name="keyed_identity_present",
        ),
        UniqueConstraint(
            "jurisdiction",
            "hash_version",
            "registration_hmac",
            name="uq_canonical_registration_hmac",
        ),
        UniqueConstraint("hash_version", "vin_hmac", name="uq_canonical_vin_hmac"),
        UniqueConstraint("hash_version", "chassis_hmac", name="uq_canonical_chassis_hmac"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    vehicle_type: Mapped[str] = mapped_column(String(8), nullable=False)
    variant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicle_variants.id", ondelete="RESTRICT")
    )
    jurisdiction: Mapped[str | None] = mapped_column(String(16))
    registration_hmac: Mapped[str | None] = mapped_column(String(64))
    vin_hmac: Mapped[str | None] = mapped_column(String(64))
    chassis_hmac: Mapped[str | None] = mapped_column(String(64))
    hash_version: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    identity_status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
