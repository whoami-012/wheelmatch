"""Add Phase 3 provider-neutral identity verification attempts and projection.

Revision ID: 0005_phase3_identity_verify
Revises: 0004_phase3_media_processing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase3_identity_verify"
down_revision: str | None = "0004_phase3_media_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_identifier", sa.String(40), nullable=False),
        sa.Column("provider_reference", sa.String(200), nullable=True),
        sa.Column("provider_result_event_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("assurance_level", sa.String(24), nullable=True),
        sa.Column("safe_failure_code", sa.String(64), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempt_number > 0",
            name=op.f("ck_identity_verifications_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('session_pending', 'pending', 'manual_review', 'verified', "
            "'failed', 'expired', 'revoked')",
            name=op.f("ck_identity_verifications_status_valid"),
        ),
        sa.CheckConstraint(
            "assurance_level IS NULL OR assurance_level IN ('basic', 'standard', 'enhanced')",
            name=op.f("ck_identity_verifications_assurance_level_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'verified' AND verified_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND expires_at > verified_at AND assurance_level IS NOT NULL "
            "AND safe_failure_code IS NULL AND revoked_at IS NULL) OR status <> 'verified'",
            name=op.f("ck_identity_verifications_verified_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND safe_failure_code IS NOT NULL AND verified_at IS NULL "
            "AND expires_at IS NULL AND revoked_at IS NULL) OR status <> 'failed'",
            name=op.f("ck_identity_verifications_failed_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR status <> 'revoked'",
            name=op.f("ck_identity_verifications_revoked_evidence_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_identity_verifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_verifications")),
        sa.UniqueConstraint("user_id", "attempt_number", name="uq_identity_verification_attempt"),
        sa.UniqueConstraint(
            "provider_identifier",
            "provider_reference",
            name="uq_identity_verification_provider_reference",
        ),
        sa.UniqueConstraint(
            "provider_identifier",
            "provider_result_event_id",
            name="uq_identity_verification_provider_result",
        ),
    )
    op.create_index(
        "ix_identity_verifications_user_status_created",
        "identity_verifications",
        ["user_id", "status", "created_at", "id"],
    )
    op.create_index(
        "uq_identity_verification_active_user",
        "identity_verifications",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('session_pending', 'pending', 'manual_review')"),
    )

    op.create_table(
        "user_verification_states",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_status", sa.String(24), nullable=False),
        sa.Column("assurance_level", sa.String(24), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("safe_failure_code", sa.String(64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_status IN ('session_pending', 'pending', 'manual_review', 'verified', "
            "'failed', 'expired', 'revoked')",
            name=op.f("ck_user_verification_states_status_valid"),
        ),
        sa.CheckConstraint(
            "assurance_level IS NULL OR assurance_level IN ('basic', 'standard', 'enhanced')",
            name=op.f("ck_user_verification_states_assurance_level_valid"),
        ),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_user_verification_states_version_positive")
        ),
        sa.CheckConstraint(
            "(effective_status = 'verified' AND verified_at IS NOT NULL "
            "AND expires_at IS NOT NULL AND expires_at > verified_at "
            "AND assurance_level IS NOT NULL AND safe_failure_code IS NULL "
            "AND revoked_at IS NULL) OR effective_status <> 'verified'",
            name=op.f("ck_user_verification_states_verified_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(effective_status = 'failed' AND safe_failure_code IS NOT NULL) "
            "OR effective_status <> 'failed'",
            name=op.f("ck_user_verification_states_failed_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(effective_status = 'revoked' AND revoked_at IS NOT NULL) "
            "OR effective_status <> 'revoked'",
            name=op.f("ck_user_verification_states_revoked_evidence_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["current_attempt_id"],
            ["identity_verifications.id"],
            name=op.f("fk_user_verification_states_current_attempt_id_identity_verifications"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_verification_states_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_verification_states")),
    )
    op.create_index(
        "ix_user_verification_states_status_updated",
        "user_verification_states",
        ["effective_status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_verification_states_status_updated", table_name="user_verification_states"
    )
    op.drop_table("user_verification_states")
    op.drop_index("uq_identity_verification_active_user", table_name="identity_verifications")
    op.drop_index(
        "ix_identity_verifications_user_status_created", table_name="identity_verifications"
    )
    op.drop_table("identity_verifications")
