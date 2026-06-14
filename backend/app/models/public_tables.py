"""
READ-ONLY mirrors of the `public.*` tenant tables we read across all tenants.

These Table definitions are owned by sop-hub's Supabase migration tree, NOT by
this repo's Alembic — they are declared here only so the service-role session
can SELECT from them with typed columns. This metadata is **never** passed to
Alembic and **never** create_all-ed against a real Postgres (only against the
in-memory SQLite test DB, where the `public` schema is ATTACH-ed). We declare
just the columns Phase 1 reads; the live tables have many more (e.g. the
evolving `feature_*` flag columns, which we read dynamically via SELECT *).

`audit_trail.organization_id` is the workspace key (sop-hub's audit writer keys
the tenant by `organization_id`; it equals `workspaces.id`).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
)

from app.models.tables import GUID

# Separate metadata so this never mingles with the `platform` schema Alembic owns.
public_metadata = MetaData(schema="public")

workspaces = Table(
    "workspaces",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("name", Text, nullable=False),
    Column("slug", Text),
    Column("is_suspended", Boolean),
    Column("created_at", DateTime(timezone=True)),
    # NOTE: `feature_*` flag columns + plan/billing columns also exist on the
    # live table; feature flags are read via SELECT * (see tenant_directory).
)

users = Table(
    "users",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("email", Text, nullable=False),
    Column("first_name", Text),
    Column("last_name", Text),
    Column("role", Text),
    Column("status", Text),
    Column("workspace_id", GUID),
    Column("last_active_at", DateTime(timezone=True)),
    Column("login_count", Integer),
    Column("created_at", DateTime(timezone=True)),
)

audit_trail = Table(
    "audit_trail",
    public_metadata,
    Column("audit_id", GUID, primary_key=True),
    Column("timestamp", DateTime(timezone=True)),
    Column("event_type", Text),
    Column("action", Text),
    Column("actor_id", GUID),
    Column("actor_email", Text),
    Column("actor_name", Text),
    Column("organization_id", GUID),  # == workspaces.id
)
