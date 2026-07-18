"""Add reusable ownership evidence to listing submission.

Revision ID: 0008_phase3_ownership_reuse
Revises: 0007_phase3_listing_submit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_phase3_ownership_reuse"
down_revision: str | None = "0007_phase3_listing_submit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_vehicles",
        sa.Column("identity_status", sa.String(24), server_default="active", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_canonical_vehicles_identity_status_valid"),
        "canonical_vehicles",
        "identity_status IN ('active', 'disputed', 'transferred', 'stolen', "
        "'written_off', 'fraud_review')",
    )
    op.alter_column("canonical_vehicles", "identity_status", server_default=None)

    op.add_column(
        "listing_submission_attempts",
        sa.Column("ownership_reused", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "listing_submission_attempts",
        sa.Column("ownership_reuse_policy_version", sa.Integer()),
    )
    op.add_column(
        "listing_submission_attempts",
        sa.Column("ownership_effective_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        op.f("ck_listing_submission_attempts_ownership_reuse_evidence_valid"),
        "listing_submission_attempts",
        "(ownership_reused = false AND ownership_reuse_policy_version IS NULL "
        "AND ownership_effective_expires_at IS NULL) OR "
        "(ownership_reused = true AND ownership_verification_id IS NOT NULL "
        "AND ownership_reuse_policy_version > 0 "
        "AND ownership_effective_expires_at IS NOT NULL)",
    )
    op.alter_column("listing_submission_attempts", "ownership_reused", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_listing_submission_attempts_ownership_reuse_evidence_valid"),
        "listing_submission_attempts",
        type_="check",
    )
    op.drop_column("listing_submission_attempts", "ownership_effective_expires_at")
    op.drop_column("listing_submission_attempts", "ownership_reuse_policy_version")
    op.drop_column("listing_submission_attempts", "ownership_reused")
    op.drop_constraint(
        op.f("ck_canonical_vehicles_identity_status_valid"),
        "canonical_vehicles",
        type_="check",
    )
    op.drop_column("canonical_vehicles", "identity_status")
