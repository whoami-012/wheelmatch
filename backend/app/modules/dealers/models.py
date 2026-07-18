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


class DealerOrganization(Base):
    __tablename__ = "dealer_organizations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="status_valid"),
        CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name="verification_status_valid",
        ),
        Index("ix_dealer_organizations_status", "status", "verification_status", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    authorization_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DealerMembership(Base):
    __tablename__ = "dealer_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_dealer_membership_org_user"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'inventory_manager', 'sales_agent')", name="role_valid"
        ),
        CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'left', 'revoked')", name="status_valid"
        ),
        CheckConstraint(
            "(status = 'invited' AND accepted_at IS NULL) OR status = 'revoked' OR "
            "(status IN ('active', 'suspended', 'left') AND accepted_at IS NOT NULL)",
            name="acceptance_state_valid",
        ),
        CheckConstraint(
            "status <> 'suspended' OR suspended_at IS NOT NULL", name="suspension_state_valid"
        ),
        CheckConstraint("status <> 'left' OR left_at IS NOT NULL", name="left_state_valid"),
        CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL", name="revoked_state_valid"
        ),
        Index("ix_dealer_memberships_user_status", "user_id", "status", "id"),
        Index("ix_dealer_memberships_org_status_role", "organization_id", "status", "role"),
        Index("ix_dealer_memberships_invite_expiry", "invite_expires_at", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("dealer_organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    invitation_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
