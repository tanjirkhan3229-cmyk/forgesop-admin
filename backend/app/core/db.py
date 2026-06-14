"""
Database engine + session for the admin service.

The console connects to the SHARED Supabase Postgres as the SERVICE-ROLE
Postgres role (RLS-bypassing) and pins `search_path = platform, public` so
that:
  * operator tables resolve in `platform` (owned by THIS service's Alembic);
  * cross-tenant reads of `public.*` resolve without schema-qualifying.

This service NEVER inserts into a tenant table. The only writes it makes to
`public` are the `public.workspaces.feature_*` columns via `apply_plan`
(Phase 2). See CLAUDE.md.
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# search_path is set per-connection. asyncpg takes it via server_settings.
# Supabase (and managed Postgres generally) require TLS, so we pass an SSL
# context — asyncpg does NOT enable TLS by default. Verification follows
# DB_SSL_VERIFY (off for the Supabase pooler's self-signed chain).
_connect_args: dict = {}
if settings.DATABASE_URL.startswith("postgresql"):
    import ssl

    _ssl_ctx = ssl.create_default_context()
    if not settings.DB_SSL_VERIFY:
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
    _connect_args = {
        "server_settings": {"search_path": "platform,public"},
        "ssl": _ssl_ctx,
    }

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a service-role session.

    Tests override this via app.dependency_overrides to inject a SQLite
    session bound to an ATTACH-ed `platform` schema.
    """
    async with SessionLocal() as session:
        yield session
