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


class IdentityVerification(TimestampMixin, Base):
    __tablename__ = "identity_verifications"
    __table_args__ = (
        UniqueConstraint("user_id", "attempt_number", name="uq_identity_verification_attempt"),
        UniqueConstraint(
            "provider_identifier",
            "provider_reference",
            name="uq_identity_verification_provider_reference",
        ),
        UniqueConstraint(
            "provider_identifier",
            "provider_result_event_id",
            name="uq_identity_verification_provider_result",
        ),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "status IN ('session_pending', 'pending', 'manual_review', 'verified', "
            "'failed', 'expired', 'revoked')",
            name="status_valid",
        ),
        CheckConstraint(
            "assurance_level IS NULL OR assurance_level IN ('basic', 'standard', 'enhanced')",
            name="assurance_level_valid",
        ),
        CheckConstraint(
            "(status = 'verified' AND verified_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND expires_at > verified_at AND assurance_level IS NOT NULL "
            "AND safe_failure_code IS NULL AND revoked_at IS NULL) OR status <> 'verified'",
            name="verified_evidence_valid",
        ),
        CheckConstraint(
            "(status = 'failed' AND safe_failure_code IS NOT NULL AND verified_at IS NULL "
            "AND expires_at IS NULL AND revoked_at IS NULL) OR status <> 'failed'",
            name="failed_evidence_valid",
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR status <> 'revoked'",
            name="revoked_evidence_valid",
        ),
        Index(
            "ix_identity_verifications_user_status_created",
            "user_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "uq_identity_verification_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('session_pending', 'pending', 'manual_review')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_identifier: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_result_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="session_pending")
    assurance_level: Mapped[str | None] = mapped_column(String(24), nullable=True)
    safe_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserVerificationState(Base):
    __tablename__ = "user_verification_states"
    __table_args__ = (
        CheckConstraint(
            "effective_status IN ('session_pending', 'pending', 'manual_review', 'verified', "
            "'failed', 'expired', 'revoked')",
            name="status_valid",
        ),
        CheckConstraint(
            "assurance_level IS NULL OR assurance_level IN ('basic', 'standard', 'enhanced')",
            name="assurance_level_valid",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(effective_status = 'verified' AND verified_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND expires_at > verified_at AND assurance_level IS NOT NULL "
            "AND safe_failure_code IS NULL AND revoked_at IS NULL) "
            "OR effective_status <> 'verified'",
            name="verified_evidence_valid",
        ),
        CheckConstraint(
            "(effective_status = 'failed' AND safe_failure_code IS NOT NULL) "
            "OR effective_status <> 'failed'",
            name="failed_evidence_valid",
        ),
        CheckConstraint(
            "(effective_status = 'revoked' AND revoked_at IS NOT NULL) "
            "OR effective_status <> 'revoked'",
            name="revoked_evidence_valid",
        ),
        Index("ix_user_verification_states_status_updated", "effective_status", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    current_attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("identity_verifications.id"), nullable=False
    )
    effective_status: Mapped[str] = mapped_column(String(24), nullable=False)
    assurance_level: Mapped[str | None] = mapped_column(String(24), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    safe_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
