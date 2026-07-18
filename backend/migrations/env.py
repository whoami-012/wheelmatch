from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.core.database.base import Base
from app.core.idempotency import models as idempotency_models  # noqa: F401
from app.core.outbox import models as outbox_models  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.catalogue import models as catalogue_models  # noqa: F401
from app.modules.dealers import models as dealer_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.listings import models as listing_models  # noqa: F401
from app.modules.listings import submission_models as listing_submission_models  # noqa: F401
from app.modules.locations import models as location_models  # noqa: F401
from app.modules.locations.models import GeographyPoint
from app.modules.media import models as media_models  # noqa: F401
from app.modules.profiles import models as profile_models  # noqa: F401
from app.modules.verification import models as verification_models  # noqa: F401
from app.modules.verification import ownership_models as verification_ownership_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url", get_settings().database_url.get_secret_value().replace("%", "%%")
)
target_metadata = Base.metadata
ischema_names["geography"] = GeographyPoint


def include_name(name: str | None, type_: str, _parent_names: dict[str, str | None]) -> bool:
    """Exclude PostGIS extension objects that are not owned by application metadata."""
    if type_ == "table":
        return name in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
