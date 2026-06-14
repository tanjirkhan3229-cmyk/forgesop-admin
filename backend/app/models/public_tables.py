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
    BigInteger,
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

# ── Module object tables (Phase 3 footprint adoption counts) ────────────────
# We declare only `workspace_id` (+ the pk) because footprints only COUNT rows
# per workspace. Names match sop-hub's live tables: `incidents` ships as
# `ehs_incidents`; the rest are 1:1. All are keyed by `workspace_id` (unlike
# audit_trail, which uses `organization_id`).

sops = Table(
    "sops",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("workspace_id", GUID),
    Column("status", Text),
    Column("created_at", DateTime(timezone=True)),
)

ehs_incidents = Table(
    "ehs_incidents",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("workspace_id", GUID),
    Column("status", Text),
    Column("created_at", DateTime(timezone=True)),
)

capas = Table(
    "capas",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("workspace_id", GUID),
    Column("status", Text),
    Column("created_at", DateTime(timezone=True)),
)

risks = Table(
    "risks",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("workspace_id", GUID),
    Column("status", Text),
    Column("created_at", DateTime(timezone=True)),
)

# Storage source. document_versions.size_bytes is the canonical per-file byte
# count; footprints sum it per workspace. (Other *_attachments tables also carry
# size_bytes; document_versions is the dominant store and the one we sum here.)
document_versions = Table(
    "document_versions",
    public_metadata,
    Column("id", GUID, primary_key=True),
    Column("workspace_id", GUID),
    Column("size_bytes", BigInteger),
    Column("uploaded_at", DateTime(timezone=True)),
)
