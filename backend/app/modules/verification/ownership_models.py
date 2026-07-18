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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base, TimestampMixin
from app.core.ids import uuid7


class VehicleOwnershipVerification(TimestampMixin, Base):
    __tablename__ = "vehicle_ownership_verifications"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "canonical_vehicle_id",
            "attempt_number",
            name="uq_vehicle_ownership_attempt",
        ),
        UniqueConstraint(
            "provider_identifier",
            "provider_reference",
            name="uq_vehicle_ownership_provider_reference",
        ),
        UniqueConstraint(
            "provider_identifier",
            "provider_result_event_id",
            name="uq_vehicle_ownership_provider_result",
        ),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "identity_projection_version > 0", name="identity_projection_version_positive"
        ),
        CheckConstraint("vehicle_identity_version > 0", name="vehicle_identity_version_positive"),
        CheckConstraint("hash_version > 0", name="hash_version_positive"),
        CheckConstraint(
            "provider_result_version IS NULL OR provider_result_version > 0",
            name="provider_result_version_positive",
        ),
        CheckConstraint(
            "ownership_basis IN ('registered_owner', 'company_vehicle', "
            "'financed_or_leased', 'inherited', 'authorized_representative')",
            name="ownership_basis_valid",
        ),
        CheckConstraint(
            "status IN ('session_pending', 'pending', 'manual_review', 'verified', "
            "'failed', 'expired', 'revoked')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'verified' AND verified_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND expires_at > verified_at AND safe_failure_code IS NULL "
            "AND revoked_at IS NULL) OR status <> 'verified'",
            name="verified_evidence_valid",
        ),
        CheckConstraint(
            "(status = 'failed' AND safe_failure_code IS NOT NULL AND verified_at IS NULL "
            "AND expires_at IS NULL AND revoked_at IS NULL) OR status <> 'failed'",
            name="failed_evidence_valid",
        ),
        CheckConstraint(
            "(status = 'manual_review' AND safe_failure_code IS NOT NULL) "
            "OR status <> 'manual_review'",
            name="manual_review_evidence_valid",
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR status <> 'revoked'",
            name="revoked_evidence_valid",
        ),
        Index(
            "ix_vehicle_ownership_owner_vehicle_created",
            "owner_user_id",
            "canonical_vehicle_id",
            "created_at",
            "id",
        ),
        Index("ix_vehicle_ownership_listing_created", "listing_id", "created_at", "id"),
        Index(
            "uq_vehicle_ownership_unresolved_owner_vehicle",
            "owner_user_id",
            "canonical_vehicle_id",
            unique=True,
            postgresql_where=text(
                "status IN ('session_pending', 'pending', 'manual_review') "
                "AND superseded_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_vehicle_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("canonical_vehicles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_verification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("identity_verifications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    identity_projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_identity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    hash_version: Mapped[int] = mapped_column(Integer, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(16), nullable=False)
    ownership_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    material_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_identifier: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(200))
    provider_result_event_id: Mapped[str | None] = mapped_column(String(200))
    provider_result_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="session_pending")
    safe_failure_code: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationDocumentRef(Base):
    __tablename__ = "verification_document_refs"
    __table_args__ = (
        UniqueConstraint(
            "provider_identifier",
            "object_reference",
            name="uq_verification_document_provider_object",
        ),
        Index("ix_verification_document_retention", "retention_expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    ownership_verification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("vehicle_ownership_verifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_identifier: Mapped[str] = mapped_column(String(40), nullable=False)
    object_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
