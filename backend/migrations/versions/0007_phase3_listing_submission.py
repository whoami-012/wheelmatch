"""Add personal listing submission readiness state.

Revision ID: 0007_phase3_listing_submit
Revises: 0006_phase3_vehicle_ownership
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase3_listing_submit"
down_revision: str | None = "0006_phase3_vehicle_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("publication_status", sa.String(16), server_default="private", nullable=False),
    )
    op.add_column(
        "listings",
        sa.Column("moderation_status", sa.String(16), server_default="not_started", nullable=False),
    )
    op.add_column("listings", sa.Column("submitted_listing_version", sa.Integer()))
    op.add_column("listings", sa.Column("submitted_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        op.f("ck_listings_publication_status_valid"),
        "listings",
        "publication_status IN ('private', 'pending')",
    )
    op.create_check_constraint(
        op.f("ck_listings_moderation_status_valid"),
        "listings",
        "moderation_status IN ('not_started', 'pending')",
    )
    op.create_check_constraint(
        op.f("ck_listings_submitted_listing_version_positive"),
        "listings",
        "submitted_listing_version IS NULL OR submitted_listing_version > 0",
    )
    op.create_check_constraint(
        op.f("ck_listings_submission_timestamp_consistent"),
        "listings",
        "(submitted_listing_version IS NULL AND submitted_at IS NULL) OR "
        "(submitted_listing_version IS NOT NULL AND submitted_at IS NOT NULL)",
    )
    op.alter_column("listings", "publication_status", server_default=None)
    op.alter_column("listings", "moderation_status", server_default=None)

    op.create_table(
        "listing_submission_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_version", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_status", sa.String(32), nullable=False),
        sa.Column("identity_verification_id", postgresql.UUID(as_uuid=True)),
        sa.Column("identity_projection_version", sa.Integer()),
        sa.Column("ownership_verification_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ownership_result_version", sa.Integer()),
        sa.Column("ownership_material_fingerprint", sa.String(64)),
        sa.Column("media_set_fingerprint", sa.String(64), nullable=False),
        sa.Column("media_set_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("blocker_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "listing_version > 0",
            name=op.f("ck_listing_submission_attempts_listing_version_positive"),
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name=op.f("ck_listing_submission_attempts_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "media_set_version > 0",
            name=op.f("ck_listing_submission_attempts_media_set_version_positive"),
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name=op.f("ck_listing_submission_attempts_policy_version_positive"),
        ),
        sa.CheckConstraint(
            "identity_projection_version IS NULL OR identity_projection_version > 0",
            name=op.f("ck_listing_submission_attempts_identity_projection_version_positive"),
        ),
        sa.CheckConstraint(
            "ownership_result_version IS NULL OR ownership_result_version > 0",
            name=op.f("ck_listing_submission_attempts_ownership_result_version_positive"),
        ),
        sa.CheckConstraint(
            "submission_status IN ('blocked', 'verification_pending', 'moderation_pending')",
            name=op.f("ck_listing_submission_attempts_submission_status_valid"),
        ),
        sa.CheckConstraint(
            "cardinality(blocker_codes) <= 10",
            name=op.f("ck_listing_submission_attempts_blocker_codes_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            ondelete="RESTRICT",
            name=op.f("fk_listing_submission_attempts_listing_id_listings"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_listing_submission_attempts_actor_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_listing_submission_attempts_owner_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["identity_verification_id"],
            ["identity_verifications.id"],
            ondelete="RESTRICT",
            name=op.f(
                "fk_listing_submission_attempts_identity_verification_id_identity_verifications"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["ownership_verification_id"],
            ["vehicle_ownership_verifications.id"],
            ondelete="RESTRICT",
            name=op.f(
                "fk_listing_submission_attempts_ownership_verification_id_vehicle_ownership_verifications"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing_submission_attempts")),
        sa.UniqueConstraint("listing_id", "attempt_number", name="uq_listing_submission_attempt"),
    )
    op.create_index(
        "ix_listing_submission_current",
        "listing_submission_attempts",
        ["listing_id", "listing_version", "superseded_at", "attempt_number"],
    )
    op.create_index(
        "uq_listing_submission_active_version",
        "listing_submission_attempts",
        ["listing_id", "listing_version"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_listing_submission_active_version", table_name="listing_submission_attempts")
    op.drop_index("ix_listing_submission_current", table_name="listing_submission_attempts")
    op.drop_table("listing_submission_attempts")
    op.drop_constraint(
        op.f("ck_listings_submission_timestamp_consistent"), "listings", type_="check"
    )
    op.drop_constraint(
        op.f("ck_listings_submitted_listing_version_positive"), "listings", type_="check"
    )
    op.drop_constraint(op.f("ck_listings_moderation_status_valid"), "listings", type_="check")
    op.drop_constraint(op.f("ck_listings_publication_status_valid"), "listings", type_="check")
    op.drop_column("listings", "submitted_at")
    op.drop_column("listings", "submitted_listing_version")
    op.drop_column("listings", "moderation_status")
    op.drop_column("listings", "publication_status")
