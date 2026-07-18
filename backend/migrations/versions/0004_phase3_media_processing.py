"""Add Phase 3 private media processing evidence and derivatives.

Revision ID: 0004_phase3_media_processing
Revises: 0003_phase2_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase3_media_processing"
down_revision: str | None = "0003_phase2_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_listing_media_status_valid"), "listing_media", type_="check")
    op.create_check_constraint(
        op.f("ck_listing_media_status_valid"),
        "listing_media",
        "status IN ('intent_created', 'processing', 'scanning', 'moderation_pending', "
        "'rejected', 'removed', 'expired', 'failed')",
    )
    op.add_column(
        "listing_media", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("listing_media", sa.Column("failure_code", sa.String(64), nullable=True))

    op.create_table(
        "media_processing_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_version", sa.Integer(), nullable=False),
        sa.Column("processor_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_checksum_sha256", sa.String(64), nullable=True),
        sa.Column("detected_content_type", sa.String(40), nullable=True),
        sa.Column("source_format", sa.String(16), nullable=True),
        sa.Column("source_width", sa.Integer(), nullable=True),
        sa.Column("source_height", sa.Integer(), nullable=True),
        sa.Column("sanitized_checksum_sha256", sa.String(64), nullable=True),
        sa.Column("perceptual_hash", sa.String(32), nullable=True),
        sa.Column("scanner_status", sa.String(16), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'moderation_pending', 'rejected', 'failed')",
            name=op.f("ck_media_processing_evidence_status_valid"),
        ),
        sa.CheckConstraint(
            "processing_version > 0",
            name=op.f("ck_media_processing_evidence_processing_version_positive"),
        ),
        sa.CheckConstraint(
            "attempt_count > 0",
            name=op.f("ck_media_processing_evidence_attempt_count_positive"),
        ),
        sa.CheckConstraint(
            "(status IN ('processing', 'moderation_pending') AND failure_code IS NULL) OR "
            "(status IN ('rejected', 'failed') AND failure_code IS NOT NULL)",
            name=op.f("ck_media_processing_evidence_failure_code_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["listing_media.id"],
            name=op.f("fk_media_processing_evidence_media_id_listing_media"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_processing_evidence")),
        sa.UniqueConstraint("media_id", "processing_version", name="uq_media_processing_version"),
    )
    op.create_index(
        "ix_media_processing_status_lease",
        "media_processing_evidence",
        ["status", "lease_expires_at", "id"],
    )

    op.create_table(
        "media_derivatives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('thumbnail', 'medium', 'large')",
            name=op.f("ck_media_derivatives_kind_valid"),
        ),
        sa.CheckConstraint(
            "processing_version > 0",
            name=op.f("ck_media_derivatives_processing_version_positive"),
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0",
            name=op.f("ck_media_derivatives_dimensions_positive"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_media_derivatives_size_positive")),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["listing_media.id"],
            name=op.f("fk_media_derivatives_media_id_listing_media"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["media_processing_evidence.id"],
            name=op.f("fk_media_derivatives_evidence_id_media_processing_evidence"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_derivatives")),
        sa.UniqueConstraint("object_key", name=op.f("uq_media_derivatives_object_key")),
        sa.UniqueConstraint(
            "media_id", "processing_version", "kind", name="uq_media_derivative_kind"
        ),
    )
    op.create_index(
        "ix_media_derivatives_media_version",
        "media_derivatives",
        ["media_id", "processing_version", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_derivatives_media_version", table_name="media_derivatives")
    op.drop_table("media_derivatives")
    op.drop_index("ix_media_processing_status_lease", table_name="media_processing_evidence")
    op.drop_table("media_processing_evidence")
    op.drop_column("listing_media", "failure_code")
    op.drop_column("listing_media", "processed_at")
    op.drop_constraint(op.f("ck_listing_media_status_valid"), "listing_media", type_="check")
    op.create_check_constraint(
        op.f("ck_listing_media_status_valid"),
        "listing_media",
        "status IN ('intent_created', 'processing', 'removed', 'expired', 'failed')",
    )
