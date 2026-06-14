"""
Alembic environment — scoped to the `platform` schema ONLY.

Guardrails (see CLAUDE.md):
  * The Alembic version table lives in `platform` (`version_table_schema`),
    so this tree's bookkeeping never lands in `public`.
  * `include_object` rejects any object whose schema is not `platform`, so
    autogenerate can never propose DDL against `public` (sop-hub's tree owns
    `public`).
  * The `platform` schema is created (IF NOT EXISTS) before migrations run so
    the version table has somewhere to live.

The connection uses an async engine built from DATABASE_URL (the service-role
connection). Migrations themselves are plain SQL.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.models.tables import metadata as target_metadata

config = context.config
# Escape '%' so ConfigParser doesn't treat URL-encoded chars in the password
# (e.g. %2B, %23) as interpolation syntax. BasicInterpolation restores them on
# read, so the engine still receives the correct URL.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

PLATFORM_SCHEMA = "platform"


def include_object(object, name, type_, reflected, compare_to) -> bool:
    """Only ever touch the `platform` schema."""
    schema = getattr(object, "schema", None)
    if type_ in ("table", "column", "index", "unique_constraint", "foreign_key_constraint"):
        # `schema` is None for the default search-path schema; our tables are
        # all explicitly schema='platform', so anything else is foreign.
        return schema == PLATFORM_SCHEMA
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=PLATFORM_SCHEMA,
        compare_type=True,
    )


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PLATFORM_SCHEMA}"'))
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Supabase requires TLS; asyncpg does not enable it by default.
    connect_args: dict = {}
    if settings.DATABASE_URL.startswith("postgresql"):
        import ssl

        ctx = ssl.create_default_context()
        if not settings.DB_SSL_VERIFY:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ctx

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        # Explicitly commit: env.py runs CREATE SCHEMA before Alembic's own
        # transaction, so without this the work sits in the connection's
        # auto-begun transaction and SQLAlchemy 2.0 rolls it back on close.
        await connection.commit()
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=PLATFORM_SCHEMA,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
