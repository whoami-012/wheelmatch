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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database.base import Base
from app.core.ids import uuid7


class ListingSubmissionAttempt(Base):
    __tablename__ = "listing_submission_attempts"
    __table_args__ = (
        UniqueConstraint("listing_id", "attempt_number", name="uq_listing_submission_attempt"),
        CheckConstraint("listing_version > 0", name="listing_version_positive"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint("media_set_version > 0", name="media_set_version_positive"),
        CheckConstraint("policy_version > 0", name="policy_version_positive"),
        CheckConstraint(
            "identity_projection_version IS NULL OR identity_projection_version > 0",
            name="identity_projection_version_positive",
        ),
        CheckConstraint(
            "ownership_result_version IS NULL OR ownership_result_version > 0",
            name="ownership_result_version_positive",
        ),
        CheckConstraint(
            "(ownership_reused = false AND ownership_reuse_policy_version IS NULL "
            "AND ownership_effective_expires_at IS NULL) OR "
            "(ownership_reused = true AND ownership_verification_id IS NOT NULL "
            "AND ownership_reuse_policy_version > 0 "
            "AND ownership_effective_expires_at IS NOT NULL)",
            name="ownership_reuse_evidence_valid",
        ),
        CheckConstraint(
            "submission_status IN ('blocked', 'verification_pending', 'moderation_pending')",
            name="submission_status_valid",
        ),
        CheckConstraint("cardinality(blocker_codes) <= 10", name="blocker_codes_bounded"),
        Index(
            "ix_listing_submission_current",
            "listing_id",
            "listing_version",
            "superseded_at",
            "attempt_number",
        ),
        Index(
            "uq_listing_submission_active_version",
            "listing_id",
            "listing_version",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False
    )
    listing_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submission_status: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_verification_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity_verifications.id", ondelete="RESTRICT")
    )
    identity_projection_version: Mapped[int | None] = mapped_column(Integer)
    ownership_verification_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("vehicle_ownership_verifications.id", ondelete="RESTRICT"),
    )
    ownership_result_version: Mapped[int | None] = mapped_column(Integer)
    ownership_material_fingerprint: Mapped[str | None] = mapped_column(String(64))
    ownership_reused: Mapped[bool] = mapped_column(nullable=False, default=False)
    ownership_reuse_policy_version: Mapped[int | None] = mapped_column(Integer)
    ownership_effective_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_set_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    media_set_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    blocker_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
