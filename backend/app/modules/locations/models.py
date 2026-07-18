from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.core.database.base import Base
from app.core.ids import uuid7


class GeographyPoint(UserDefinedType[Any]):
    cache_ok = True

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        super().__init__()

    def get_col_spec(self, **_kw: Any) -> str:
        return "geography(POINT,4326)"


class DealerPublicAddress(Base):
    __tablename__ = "dealer_public_addresses"
    __table_args__ = (
        CheckConstraint(
            "publication_status IN ('private', 'published')", name="publication_status_valid"
        ),
        Index(
            "ix_dealer_public_addresses_organization", "organization_id", "publication_status", "id"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("dealer_organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    exact_point: Mapped[Any] = mapped_column(GeographyPoint(), nullable=False)
    address_line: Mapped[str] = mapped_column(String(240), nullable=False)
    locality: Mapped[str] = mapped_column(String(120), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_status: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListingLocation(Base):
    __tablename__ = "listing_locations"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('approximate', 'public_business')", name="visibility_valid"
        ),
        CheckConstraint(
            "(visibility = 'approximate' AND public_address_id IS NULL) OR "
            "(visibility = 'public_business' AND public_address_id IS NOT NULL)",
            name="visibility_address_valid",
        ),
        Index("ix_listing_locations_exact_point", "exact_point", postgresql_using="gist"),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    exact_point: Mapped[Any] = mapped_column(GeographyPoint(), nullable=False)
    locality: Mapped[str] = mapped_column(String(120), nullable=False)
    coarse_area: Mapped[str] = mapped_column(String(120), nullable=False)
    coarse_cell_hmac: Mapped[str | None] = mapped_column(String(64))
    visibility: Mapped[str] = mapped_column(String(24), nullable=False)
    public_address_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dealer_public_addresses.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
