from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base
from app.core.ids import uuid7


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("owner_type IN ('user', 'dealer_organization')", name="owner_type_valid"),
        CheckConstraint("vehicle_type IN ('car', 'bike')", name="vehicle_type_valid"),
        CheckConstraint("lifecycle_status IN ('draft', 'removed')", name="lifecycle_status_valid"),
        CheckConstraint(
            "publication_status IN ('private', 'pending')", name="publication_status_valid"
        ),
        CheckConstraint(
            "moderation_status IN ('not_started', 'pending')", name="moderation_status_valid"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "submitted_listing_version IS NULL OR submitted_listing_version > 0",
            name="submitted_listing_version_positive",
        ),
        CheckConstraint(
            "(submitted_listing_version IS NULL AND submitted_at IS NULL) OR "
            "(submitted_listing_version IS NOT NULL AND submitted_at IS NOT NULL)",
            name="submission_timestamp_consistent",
        ),
        CheckConstraint(
            "(owner_type = 'user' AND owner_user_id IS NOT NULL "
            "AND owner_organization_id IS NULL) OR "
            "(owner_type = 'dealer_organization' AND owner_user_id IS NULL "
            "AND owner_organization_id IS NOT NULL)",
            name="exactly_one_owner",
        ),
        Index(
            "ix_listings_user_status_updated",
            "owner_user_id",
            "lifecycle_status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_listings_organization_status_updated",
            "owner_organization_id",
            "lifecycle_status",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    owner_type: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    owner_organization_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealer_organizations.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    vehicle_type: Mapped[str] = mapped_column(String(8), nullable=False)
    variant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicle_variants.id", ondelete="RESTRICT")
    )
    canonical_vehicle_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("canonical_vehicles.id", ondelete="RESTRICT")
    )
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    publication_status: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    moderation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_started"
    )
    submitted_listing_version: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text())
    asking_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VehicleSpec(Base):
    __tablename__ = "vehicle_specs"
    __table_args__ = (
        CheckConstraint("manufacture_year BETWEEN 1886 AND 2100", name="manufacture_year_valid"),
        CheckConstraint("odometer_km >= 0", name="odometer_nonnegative"),
        CheckConstraint("ownership_count BETWEEN 1 AND 20", name="ownership_count_valid"),
        CheckConstraint(
            "fuel_type IN ('petrol', 'diesel', 'electric', 'hybrid', 'cng', 'lpg', 'other')",
            name="fuel_type_valid",
        ),
        CheckConstraint(
            "transmission IN ('manual', 'automatic', 'cvt', 'single_speed', 'other')",
            name="transmission_valid",
        ),
        CheckConstraint(
            "condition IN ('excellent', 'good', 'fair', 'project')", name="condition_valid"
        ),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    manufacture_year: Mapped[int] = mapped_column(Integer, nullable=False)
    odometer_km: Mapped[int] = mapped_column(Integer, nullable=False)
    fuel_type: Mapped[str] = mapped_column(String(16), nullable=False)
    transmission: Mapped[str] = mapped_column(String(16), nullable=False)
    ownership_count: Mapped[int] = mapped_column(Integer, nullable=False)
    colour: Mapped[str] = mapped_column(String(40), nullable=False)
    condition: Mapped[str] = mapped_column(String(16), nullable=False)


class CarSpec(Base):
    __tablename__ = "car_specs"
    __table_args__ = (
        CheckConstraint("seats BETWEEN 1 AND 20", name="seats_valid"),
        CheckConstraint(
            "engine_cc IS NULL OR engine_cc BETWEEN 1 AND 20000", name="engine_cc_valid"
        ),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    body_type: Mapped[str] = mapped_column(String(32), nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_cc: Mapped[int | None] = mapped_column(Integer)
    drivetrain: Mapped[str] = mapped_column(String(16), nullable=False)
    emission_standard: Mapped[str | None] = mapped_column(String(24))


class BikeSpec(Base):
    __tablename__ = "bike_specs"
    __table_args__ = (
        CheckConstraint(
            "engine_cc IS NULL OR engine_cc BETWEEN 1 AND 5000", name="engine_cc_valid"
        ),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    bike_category: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_cc: Mapped[int | None] = mapped_column(Integer)
    start_type: Mapped[str] = mapped_column(String(24), nullable=False)
    braking_system: Mapped[str] = mapped_column(String(24), nullable=False)
