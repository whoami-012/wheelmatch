"""Create Phase 1 identity and authorization schema.

Revision ID: 0002_identity_authorization
Revises: 0001_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity_authorization"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    _create_identity_tables()
    _create_profile_tables()
    _create_dealer_tables()
    _create_audit_table()


def _create_identity_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("normalized_phone", sa.String(length=16), nullable=True),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("login_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')",
            name=op.f("ck_users_status_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR status <> 'deleted'",
            name=op.f("ck_users_deleted_state_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("ix_users_status_id", "users", ["status", "id"])
    op.create_index(
        "uq_users_normalized_email_active",
        "users",
        ["normalized_email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_users_normalized_phone_active",
        "users",
        ["normalized_phone"],
        unique=True,
        postgresql_where=sa.text("normalized_phone IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "verification_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "kind IN ('email', 'phone')", name=op.f("ck_verification_challenges_kind_valid")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0",
            name=op.f("ck_verification_challenges_attempts_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_verification_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_challenges")),
    )
    op.create_index(
        "ix_verification_challenges_expiry",
        "verification_challenges",
        ["expires_at", "consumed_at"],
    )
    op.create_index(
        "ix_verification_challenges_user_kind",
        "verification_challenges",
        ["user_id", "kind", "created_at"],
    )

    op.create_table(
        "password_recovery_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0",
            name=op.f("ck_password_recovery_challenges_attempts_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_recovery_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_recovery_challenges")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_recovery_challenges_token_hash")),
    )
    op.create_index(
        "ix_password_recovery_expiry",
        "password_recovery_challenges",
        ["expires_at", "consumed_at"],
    )
    op.create_index(
        "ix_password_recovery_user_created",
        "password_recovery_challenges",
        ["user_id", "created_at"],
    )

    op.create_table(
        "session_families",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_name", sa.String(length=120), nullable=True),
        sa.Column("device_platform", sa.String(length=40), nullable=True),
        _created_at(),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=80), nullable=True),
        sa.Column("reuse_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reuse_detected_at IS NULL OR revoked_at IS NOT NULL",
            name=op.f("ck_session_families_reuse_requires_revocation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_session_families_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_families")),
    )
    op.create_index(
        "ix_session_families_user_active",
        "session_families",
        ["user_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["session_families.id"],
            name=op.f("fk_refresh_sessions_family_id_session_families"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_session_id"],
            ["refresh_sessions.id"],
            name=op.f("fk_refresh_sessions_parent_session_id_refresh_sessions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_sessions")),
        sa.UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
    )
    op.create_index(
        "ix_refresh_sessions_active_expiry",
        "refresh_sessions",
        ["expires_at", "used_at", "revoked_at"],
    )
    op.create_index(
        "ix_refresh_sessions_family_created", "refresh_sessions", ["family_id", "created_at"]
    )

    op.create_table(
        "rate_limit_buckets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=False),
        _updated_at(),
        sa.CheckConstraint(
            "request_count >= 0", name=op.f("ck_rate_limit_buckets_request_count_nonnegative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_buckets")),
        sa.UniqueConstraint("scope", "subject_hash", name="uq_rate_limit_scope_subject"),
    )
    op.create_index("ix_rate_limit_buckets_expiry", "rate_limit_buckets", ["window_expires_at"])


def _create_profile_tables() -> None:
    op.create_table(
        "profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("home_locality", sa.String(length=120), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_profiles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_profiles")),
    )
    op.create_table(
        "seller_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("readiness_state", sa.String(length=24), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended')",
            name=op.f("ck_seller_profiles_status_valid"),
        ),
        sa.CheckConstraint(
            "readiness_state IN ('not_ready', 'ready')",
            name=op.f("ck_seller_profiles_readiness_state_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'active' OR readiness_state = 'ready'",
            name=op.f("ck_seller_profiles_active_requires_readiness"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_seller_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_seller_profiles")),
    )


def _create_dealer_tables() -> None:
    op.create_table(
        "dealer_organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name=op.f("ck_dealer_organizations_status_valid"),
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name=op.f("ck_dealer_organizations_verification_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_dealer_organizations_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dealer_organizations")),
    )
    op.create_index(
        "ix_dealer_organizations_status",
        "dealer_organizations",
        ["status", "verification_status", "id"],
    )

    op.create_table(
        "dealer_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invitation_token_hash", sa.String(length=64), nullable=True),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'inventory_manager', 'sales_agent')",
            name=op.f("ck_dealer_memberships_role_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'left', 'revoked')",
            name=op.f("ck_dealer_memberships_status_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'invited' AND accepted_at IS NULL) OR status = 'revoked' OR "
            "(status IN ('active', 'suspended', 'left') AND accepted_at IS NOT NULL)",
            name=op.f("ck_dealer_memberships_acceptance_state_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'suspended' OR suspended_at IS NOT NULL",
            name=op.f("ck_dealer_memberships_suspension_state_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'left' OR left_at IS NOT NULL",
            name=op.f("ck_dealer_memberships_left_state_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL",
            name=op.f("ck_dealer_memberships_revoked_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_dealer_memberships_invited_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["dealer_organizations.id"],
            name=op.f("fk_dealer_memberships_organization_id_dealer_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_dealer_memberships_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dealer_memberships")),
        sa.UniqueConstraint(
            "invitation_token_hash", name=op.f("uq_dealer_memberships_invitation_token_hash")
        ),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_dealer_membership_org_user"),
    )
    op.create_index(
        "ix_dealer_memberships_invite_expiry",
        "dealer_memberships",
        ["invite_expires_at", "status"],
    )
    op.create_index(
        "ix_dealer_memberships_org_status_role",
        "dealer_memberships",
        ["organization_id", "status", "role"],
    )
    op.create_index(
        "ix_dealer_memberships_user_status",
        "dealer_memberships",
        ["user_id", "status", "id"],
    )


def _create_audit_table() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["dealer_memberships.id"],
            name=op.f("fk_audit_logs_membership_id_dealer_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["dealer_organizations.id"],
            name=op.f("fk_audit_logs_organization_id_dealer_organizations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at", "id"]
    )
    op.create_index(
        "ix_audit_logs_organization_created",
        "audit_logs",
        ["organization_id", "created_at", "id"],
    )
    op.create_index(
        "ix_audit_logs_resource_created",
        "audit_logs",
        ["resource_type", "resource_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_resource_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_organization_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_dealer_memberships_user_status", table_name="dealer_memberships")
    op.drop_index("ix_dealer_memberships_org_status_role", table_name="dealer_memberships")
    op.drop_index("ix_dealer_memberships_invite_expiry", table_name="dealer_memberships")
    op.drop_table("dealer_memberships")
    op.drop_index("ix_dealer_organizations_status", table_name="dealer_organizations")
    op.drop_table("dealer_organizations")
    op.drop_table("seller_profiles")
    op.drop_table("profiles")
    op.drop_index("ix_rate_limit_buckets_expiry", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    op.drop_index("ix_refresh_sessions_family_created", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_active_expiry", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_index("ix_session_families_user_active", table_name="session_families")
    op.drop_table("session_families")
    op.drop_index("ix_password_recovery_user_created", table_name="password_recovery_challenges")
    op.drop_index("ix_password_recovery_expiry", table_name="password_recovery_challenges")
    op.drop_table("password_recovery_challenges")
    op.drop_index("ix_verification_challenges_user_kind", table_name="verification_challenges")
    op.drop_index("ix_verification_challenges_expiry", table_name="verification_challenges")
    op.drop_table("verification_challenges")
    op.drop_index("uq_users_normalized_phone_active", table_name="users")
    op.drop_index("uq_users_normalized_email_active", table_name="users")
    op.drop_index("ix_users_status_id", table_name="users")
    op.drop_table("users")
