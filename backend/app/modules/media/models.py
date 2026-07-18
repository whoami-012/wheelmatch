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

from app.core.database.base import Base
from app.core.ids import uuid7


class ListingMedia(Base):
    __tablename__ = "listing_media"
    __table_args__ = (
        CheckConstraint(
            "status IN ('intent_created', 'processing', 'scanning', 'moderation_pending', "
            "'rejected', 'removed', 'expired', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("expected_size_bytes > 0", name="expected_size_positive"),
        CheckConstraint("sort_order BETWEEN 0 AND 19", name="sort_order_valid"),
        CheckConstraint("processing_version > 0", name="processing_version_positive"),
        Index("ix_listing_media_listing_status", "listing_id", "status", "sort_order", "id"),
        Index("ix_listing_media_expiry", "status", "expires_at", "id"),
        Index(
            "uq_listing_media_active_order",
            "listing_id",
            "sort_order",
            unique=True,
            postgresql_where=text("status <> 'removed'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    listing_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    expected_content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    expected_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="intent_created")
    processing_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MediaProcessingEvidence(Base):
    __tablename__ = "media_processing_evidence"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'moderation_pending', 'rejected', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("processing_version > 0", name="processing_version_positive"),
        CheckConstraint("attempt_count > 0", name="attempt_count_positive"),
        CheckConstraint(
            "(status IN ('processing', 'moderation_pending') AND failure_code IS NULL) OR "
            "(status IN ('rejected', 'failed') AND failure_code IS NOT NULL)",
            name="failure_code_consistent",
        ),
        UniqueConstraint("media_id", "processing_version", name="uq_media_processing_version"),
        Index("ix_media_processing_status_lease", "status", "lease_expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    media_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listing_media.id", ondelete="CASCADE"), nullable=False
    )
    processing_version: Mapped[int] = mapped_column(Integer, nullable=False)
    processor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    detected_content_type: Mapped[str | None] = mapped_column(String(40))
    source_format: Mapped[str | None] = mapped_column(String(16))
    source_width: Mapped[int | None] = mapped_column(Integer)
    source_height: Mapped[int | None] = mapped_column(Integer)
    sanitized_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(32))
    scanner_status: Mapped[str | None] = mapped_column(String(16))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MediaDerivative(Base):
    __tablename__ = "media_derivatives"
    __table_args__ = (
        CheckConstraint("kind IN ('thumbnail', 'medium', 'large')", name="kind_valid"),
        CheckConstraint("processing_version > 0", name="processing_version_positive"),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        UniqueConstraint("media_id", "processing_version", "kind", name="uq_media_derivative_kind"),
        Index("ix_media_derivatives_media_version", "media_id", "processing_version", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    media_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listing_media.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("media_processing_evidence.id", ondelete="CASCADE"),
        nullable=False,
    )
    processing_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
