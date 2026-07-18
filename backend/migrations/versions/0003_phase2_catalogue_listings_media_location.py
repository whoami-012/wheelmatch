"""Create Phase 2 catalogue, listing, media, and location schema.

Revision ID: 0003_phase2_core
Revises: 0002_identity_authorization
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase2_core"
down_revision: str | None = "0002_identity_authorization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class GeographyPoint(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw: object) -> str:
        return "geography(POINT,4326)"


def upgrade() -> None:
    op.create_table(
        "vehicle_makes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("vehicle_type", sa.String(8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vehicle_type IN ('car', 'bike', 'both')",
            name=op.f("ck_vehicle_makes_vehicle_type_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_makes")),
        sa.UniqueConstraint("normalized_name", name="uq_vehicle_makes_normalized_name"),
    )
    op.create_index(
        "ix_vehicle_makes_type_name", "vehicle_makes", ["vehicle_type", "normalized_name", "id"]
    )
    op.create_table(
        "vehicle_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("make_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("vehicle_type", sa.String(8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vehicle_type IN ('car', 'bike')", name=op.f("ck_vehicle_models_vehicle_type_valid")
        ),
        sa.ForeignKeyConstraint(
            ["make_id"],
            ["vehicle_makes.id"],
            name=op.f("fk_vehicle_models_make_id_vehicle_makes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_models")),
        sa.UniqueConstraint(
            "make_id", "vehicle_type", "normalized_name", name="uq_vehicle_models_parent_name"
        ),
    )
    op.create_index(
        "ix_vehicle_models_make_type_name",
        "vehicle_models",
        ["make_id", "vehicle_type", "normalized_name", "id"],
    )
    op.create_table(
        "vehicle_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.String(160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["vehicle_models.id"],
            name=op.f("fk_vehicle_variants_model_id_vehicle_models"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_variants")),
        sa.UniqueConstraint("model_id", "normalized_name", name="uq_vehicle_variants_parent_name"),
    )
    op.create_index(
        "ix_vehicle_variants_model_name", "vehicle_variants", ["model_id", "normalized_name", "id"]
    )
    op.create_table(
        "canonical_vehicles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_type", sa.String(8), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("jurisdiction", sa.String(16), nullable=True),
        sa.Column("registration_hmac", sa.String(64), nullable=True),
        sa.Column("vin_hmac", sa.String(64), nullable=True),
        sa.Column("chassis_hmac", sa.String(64), nullable=True),
        sa.Column("hash_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vehicle_type IN ('car', 'bike')", name=op.f("ck_canonical_vehicles_vehicle_type_valid")
        ),
        sa.CheckConstraint(
            "hash_version > 0", name=op.f("ck_canonical_vehicles_hash_version_positive")
        ),
        sa.CheckConstraint(
            "registration_hmac IS NOT NULL OR vin_hmac IS NOT NULL OR chassis_hmac IS NOT NULL",
            name=op.f("ck_canonical_vehicles_keyed_identity_present"),
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["vehicle_variants.id"],
            name=op.f("fk_canonical_vehicles_variant_id_vehicle_variants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_vehicles")),
        sa.UniqueConstraint(
            "jurisdiction",
            "hash_version",
            "registration_hmac",
            name="uq_canonical_registration_hmac",
        ),
        sa.UniqueConstraint("hash_version", "vin_hmac", name="uq_canonical_vin_hmac"),
        sa.UniqueConstraint("hash_version", "chassis_hmac", name="uq_canonical_chassis_hmac"),
    )
    op.create_table(
        "listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_type", sa.String(24), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_type", sa.String(8), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canonical_vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lifecycle_status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("asking_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
            "owner_type IN ('user', 'dealer_organization')",
            name=op.f("ck_listings_owner_type_valid"),
        ),
        sa.CheckConstraint(
            "vehicle_type IN ('car', 'bike')",
            name=op.f("ck_listings_vehicle_type_valid"),
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('draft', 'removed')",
            name=op.f("ck_listings_lifecycle_status_valid"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_listings_version_positive")),
        sa.CheckConstraint(
            "(owner_type = 'user' AND owner_user_id IS NOT NULL AND "
            "owner_organization_id IS NULL) OR "
            "(owner_type = 'dealer_organization' AND owner_user_id IS NULL AND "
            "owner_organization_id IS NOT NULL)",
            name=op.f("ck_listings_exactly_one_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_listings_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_organization_id"],
            ["dealer_organizations.id"],
            name=op.f("fk_listings_owner_organization_id_dealer_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_listings_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["vehicle_variants.id"],
            name=op.f("fk_listings_variant_id_vehicle_variants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_vehicle_id"],
            ["canonical_vehicles.id"],
            name=op.f("fk_listings_canonical_vehicle_id_canonical_vehicles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listings")),
    )
    op.create_index(
        "ix_listings_user_status_updated",
        "listings",
        ["owner_user_id", "lifecycle_status", "updated_at", "id"],
    )
    op.create_index(
        "ix_listings_organization_status_updated",
        "listings",
        ["owner_organization_id", "lifecycle_status", "updated_at", "id"],
    )
    op.create_table(
        "vehicle_specs",
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manufacture_year", sa.Integer(), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=False),
        sa.Column("fuel_type", sa.String(16), nullable=False),
        sa.Column("transmission", sa.String(16), nullable=False),
        sa.Column("ownership_count", sa.Integer(), nullable=False),
        sa.Column("colour", sa.String(40), nullable=False),
        sa.Column("condition", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "manufacture_year BETWEEN 1886 AND 2100",
            name=op.f("ck_vehicle_specs_manufacture_year_valid"),
        ),
        sa.CheckConstraint("odometer_km >= 0", name=op.f("ck_vehicle_specs_odometer_nonnegative")),
        sa.CheckConstraint(
            "ownership_count BETWEEN 1 AND 20",
            name=op.f("ck_vehicle_specs_ownership_count_valid"),
        ),
        sa.CheckConstraint(
            "fuel_type IN ('petrol', 'diesel', 'electric', 'hybrid', 'cng', 'lpg', 'other')",
            name=op.f("ck_vehicle_specs_fuel_type_valid"),
        ),
        sa.CheckConstraint(
            "transmission IN ('manual', 'automatic', 'cvt', 'single_speed', 'other')",
            name=op.f("ck_vehicle_specs_transmission_valid"),
        ),
        sa.CheckConstraint(
            "condition IN ('excellent', 'good', 'fair', 'project')",
            name=op.f("ck_vehicle_specs_condition_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_vehicle_specs_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("listing_id", name=op.f("pk_vehicle_specs")),
    )
    op.create_table(
        "car_specs",
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body_type", sa.String(32), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("engine_cc", sa.Integer(), nullable=True),
        sa.Column("drivetrain", sa.String(16), nullable=False),
        sa.Column("emission_standard", sa.String(24), nullable=True),
        sa.CheckConstraint("seats BETWEEN 1 AND 20", name=op.f("ck_car_specs_seats_valid")),
        sa.CheckConstraint(
            "engine_cc IS NULL OR engine_cc BETWEEN 1 AND 20000",
            name=op.f("ck_car_specs_engine_cc_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_car_specs_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("listing_id", name=op.f("pk_car_specs")),
    )
    op.create_table(
        "bike_specs",
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bike_category", sa.String(32), nullable=False),
        sa.Column("engine_cc", sa.Integer(), nullable=True),
        sa.Column("start_type", sa.String(24), nullable=False),
        sa.Column("braking_system", sa.String(24), nullable=False),
        sa.CheckConstraint(
            "engine_cc IS NULL OR engine_cc BETWEEN 1 AND 5000",
            name=op.f("ck_bike_specs_engine_cc_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_bike_specs_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("listing_id", name=op.f("pk_bike_specs")),
    )
    op.create_table(
        "dealer_public_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exact_point", GeographyPoint(), nullable=False),
        sa.Column("address_line", sa.String(240), nullable=False),
        sa.Column("locality", sa.String(120), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "publication_status IN ('private', 'published')",
            name=op.f("ck_dealer_public_addresses_publication_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["dealer_organizations.id"],
            name=op.f("fk_dealer_public_addresses_organization_id_dealer_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dealer_public_addresses")),
    )
    op.create_index(
        "ix_dealer_public_addresses_organization",
        "dealer_public_addresses",
        ["organization_id", "publication_status", "id"],
    )
    op.create_table(
        "listing_locations",
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exact_point", GeographyPoint(), nullable=False),
        sa.Column("locality", sa.String(120), nullable=False),
        sa.Column("coarse_area", sa.String(120), nullable=False),
        sa.Column("coarse_cell_hmac", sa.String(64), nullable=True),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("public_address_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "visibility IN ('approximate', 'public_business')",
            name=op.f("ck_listing_locations_visibility_valid"),
        ),
        sa.CheckConstraint(
            "(visibility = 'approximate' AND public_address_id IS NULL) OR "
            "(visibility = 'public_business' AND public_address_id IS NOT NULL)",
            name=op.f("ck_listing_locations_visibility_address_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_listing_locations_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["public_address_id"],
            ["dealer_public_addresses.id"],
            name=op.f("fk_listing_locations_public_address_id_dealer_public_addresses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("listing_id", name=op.f("pk_listing_locations")),
    )
    op.create_index(
        "ix_listing_locations_exact_point",
        "listing_locations",
        ["exact_point"],
        postgresql_using="gist",
    )
    op.create_table(
        "listing_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(240), nullable=False),
        sa.Column("expected_content_type", sa.String(40), nullable=False),
        sa.Column("expected_size_bytes", sa.Integer(), nullable=False),
        sa.Column("expected_checksum_sha256", sa.String(64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("processing_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('intent_created', 'processing', 'removed', 'expired', 'failed')",
            name=op.f("ck_listing_media_status_valid"),
        ),
        sa.CheckConstraint(
            "expected_size_bytes > 0",
            name=op.f("ck_listing_media_expected_size_positive"),
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 0 AND 19",
            name=op.f("ck_listing_media_sort_order_valid"),
        ),
        sa.CheckConstraint(
            "processing_version > 0",
            name=op.f("ck_listing_media_processing_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_listing_media_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_listing_media_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing_media")),
        sa.UniqueConstraint("object_key", name=op.f("uq_listing_media_object_key")),
    )
    op.create_index(
        "ix_listing_media_listing_status",
        "listing_media",
        ["listing_id", "status", "sort_order", "id"],
    )
    op.create_index(
        "ix_listing_media_expiry",
        "listing_media",
        ["status", "expires_at", "id"],
    )
    op.create_index(
        "uq_listing_media_active_order",
        "listing_media",
        ["listing_id", "sort_order"],
        unique=True,
        postgresql_where=sa.text("status <> 'removed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_listing_media_active_order", table_name="listing_media")
    op.drop_index("ix_listing_media_expiry", table_name="listing_media")
    op.drop_index("ix_listing_media_listing_status", table_name="listing_media")
    op.drop_table("listing_media")
    op.drop_index("ix_listing_locations_exact_point", table_name="listing_locations")
    op.drop_table("listing_locations")
    op.drop_index(
        "ix_dealer_public_addresses_organization",
        table_name="dealer_public_addresses",
    )
    op.drop_table("dealer_public_addresses")
    op.drop_table("bike_specs")
    op.drop_table("car_specs")
    op.drop_table("vehicle_specs")
    op.drop_index("ix_listings_organization_status_updated", table_name="listings")
    op.drop_index("ix_listings_user_status_updated", table_name="listings")
    op.drop_table("listings")
    op.drop_table("canonical_vehicles")
    op.drop_index("ix_vehicle_variants_model_name", table_name="vehicle_variants")
    op.drop_table("vehicle_variants")
    op.drop_index("ix_vehicle_models_make_type_name", table_name="vehicle_models")
    op.drop_table("vehicle_models")
    op.drop_index("ix_vehicle_makes_type_name", table_name="vehicle_makes")
    op.drop_table("vehicle_makes")
