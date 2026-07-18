"""Add keyed canonical vehicle and personal ownership verification.

Revision ID: 0006_phase3_vehicle_ownership
Revises: 0005_phase3_identity_verify
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase3_vehicle_ownership"
down_revision: str | None = "0005_phase3_identity_verify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_vehicles",
        sa.Column("identity_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "canonical_vehicles",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_canonical_vehicles_identity_version_positive"),
        "canonical_vehicles",
        "identity_version > 0",
    )
    op.alter_column("canonical_vehicles", "identity_version", server_default=None)

    op.create_table(
        "vehicle_ownership_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("identity_verification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_projection_version", sa.Integer(), nullable=False),
        sa.Column("vehicle_identity_version", sa.Integer(), nullable=False),
        sa.Column("hash_version", sa.Integer(), nullable=False),
        sa.Column("jurisdiction", sa.String(16), nullable=False),
        sa.Column("ownership_basis", sa.String(32), nullable=False),
        sa.Column("material_fingerprint", sa.String(64), nullable=False),
        sa.Column("provider_identifier", sa.String(40), nullable=False),
        sa.Column("provider_reference", sa.String(200), nullable=True),
        sa.Column("provider_result_event_id", sa.String(200), nullable=True),
        sa.Column("provider_result_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
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
            name=op.f("ck_vehicle_ownership_verifications_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "identity_projection_version > 0",
            name=op.f("ck_vehicle_ownership_verifications_identity_projection_version_positive"),
        ),
        sa.CheckConstraint(
            "vehicle_identity_version > 0",
            name=op.f("ck_vehicle_ownership_verifications_vehicle_identity_version_positive"),
        ),
        sa.CheckConstraint(
            "hash_version > 0",
            name=op.f("ck_vehicle_ownership_verifications_hash_version_positive"),
        ),
        sa.CheckConstraint(
            "provider_result_version IS NULL OR provider_result_version > 0",
            name=op.f("ck_vehicle_ownership_verifications_provider_result_version_positive"),
        ),
        sa.CheckConstraint(
            "ownership_basis IN ('registered_owner', 'company_vehicle', 'financed_or_leased', "
            "'inherited', 'authorized_representative')",
            name=op.f("ck_vehicle_ownership_verifications_ownership_basis_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('session_pending', 'pending', 'manual_review', 'verified', "
            "'failed', 'expired', 'revoked')",
            name=op.f("ck_vehicle_ownership_verifications_status_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'verified' AND verified_at IS NOT NULL AND expires_at IS NOT NULL "
            "AND expires_at > verified_at AND safe_failure_code IS NULL "
            "AND revoked_at IS NULL) OR status <> 'verified'",
            name=op.f("ck_vehicle_ownership_verifications_verified_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND safe_failure_code IS NOT NULL AND verified_at IS NULL "
            "AND expires_at IS NULL AND revoked_at IS NULL) OR status <> 'failed'",
            name=op.f("ck_vehicle_ownership_verifications_failed_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'manual_review' AND safe_failure_code IS NOT NULL) "
            "OR status <> 'manual_review'",
            name=op.f("ck_vehicle_ownership_verifications_manual_review_evidence_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR status <> 'revoked'",
            name=op.f("ck_vehicle_ownership_verifications_revoked_evidence_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_vehicle_ownership_verifications_listing_id_listings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_vehicle_ownership_verifications_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_vehicle_id"],
            ["canonical_vehicles.id"],
            name=op.f("fk_vehicle_ownership_verifications_canonical_vehicle_id_canonical_vehicles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["identity_verification_id"],
            ["identity_verifications.id"],
            name=op.f(
                "fk_vehicle_ownership_verifications_identity_verification_id_identity_verifications"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_ownership_verifications")),
        sa.UniqueConstraint(
            "owner_user_id",
            "canonical_vehicle_id",
            "attempt_number",
            name="uq_vehicle_ownership_attempt",
        ),
        sa.UniqueConstraint(
            "provider_identifier",
            "provider_reference",
            name="uq_vehicle_ownership_provider_reference",
        ),
        sa.UniqueConstraint(
            "provider_identifier",
            "provider_result_event_id",
            name="uq_vehicle_ownership_provider_result",
        ),
    )
    op.create_index(
        "ix_vehicle_ownership_owner_vehicle_created",
        "vehicle_ownership_verifications",
        ["owner_user_id", "canonical_vehicle_id", "created_at", "id"],
    )
    op.create_index(
        "ix_vehicle_ownership_listing_created",
        "vehicle_ownership_verifications",
        ["listing_id", "created_at", "id"],
    )
    op.create_index(
        "uq_vehicle_ownership_unresolved_owner_vehicle",
        "vehicle_ownership_verifications",
        ["owner_user_id", "canonical_vehicle_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('session_pending', 'pending', 'manual_review') AND superseded_at IS NULL"
        ),
    )

    op.create_table(
        "verification_document_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ownership_verification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_identifier", sa.String(40), nullable=False),
        sa.Column("object_reference", sa.String(200), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ownership_verification_id"],
            ["vehicle_ownership_verifications.id"],
            name=op.f(
                "fk_verification_document_refs_ownership_verification_id_vehicle_ownership_verifications"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_document_refs")),
        sa.UniqueConstraint(
            "provider_identifier",
            "object_reference",
            name="uq_verification_document_provider_object",
        ),
    )
    op.create_index(
        "ix_verification_document_retention",
        "verification_document_refs",
        ["retention_expires_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_verification_document_retention", table_name="verification_document_refs")
    op.drop_table("verification_document_refs")
    op.drop_index(
        "uq_vehicle_ownership_unresolved_owner_vehicle",
        table_name="vehicle_ownership_verifications",
    )
    op.drop_index(
        "ix_vehicle_ownership_listing_created", table_name="vehicle_ownership_verifications"
    )
    op.drop_index(
        "ix_vehicle_ownership_owner_vehicle_created",
        table_name="vehicle_ownership_verifications",
    )
    op.drop_table("vehicle_ownership_verifications")
    op.drop_constraint(
        op.f("ck_canonical_vehicles_identity_version_positive"),
        "canonical_vehicles",
        type_="check",
    )
    op.drop_column("canonical_vehicles", "updated_at")
    op.drop_column("canonical_vehicles", "identity_version")
