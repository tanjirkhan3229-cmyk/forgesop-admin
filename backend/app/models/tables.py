"""
Core Table definitions for the `platform` schema.

These are the SQLAlchemy Core tables the app queries at runtime. The
authoritative production DDL lives in the Alembic migration
(`alembic/versions/0001_platform_schema.py`) as raw SQL — these Core
definitions mirror it so the service can read/write without an ORM layer.

Portable column types: each compiles to the real Postgres type
(`uuid`/`jsonb`/`inet`) on Postgres and degrades to a generic type on
SQLite so the test-suite can run the exact same code against an in-memory
DB. The `platform` schema is created on Postgres by the migration; in tests
it is ATTACH-ed (see tests/conftest.py).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.types import JSON

# Postgres-native on PG, portable elsewhere (tests on SQLite).
GUID = String(36).with_variant(UUID(as_uuid=False), "postgresql")
JSONB_T = JSON().with_variant(JSONB(), "postgresql")
INET_T = String(45).with_variant(INET(), "postgresql")
# numeric(6,2) on PG; a plain float on SQLite so test equality is exact.
NUMERIC_T = Float().with_variant(Numeric(6, 2), "postgresql")

metadata = MetaData(schema="platform")

# Operator allowlist. Operators are NOT rows in public.users and have no
# workspace_id — see CLAUDE.md "Operator identity".
platform_admins = Table(
    "platform_admins",
    metadata,
    Column("id", GUID, primary_key=True),
    Column("email", Text, unique=True, nullable=False),
    Column("role", Text, nullable=False),  # CHECK enforced in migration DDL
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

# Hash-chained log of every operator mutation + sensitive read.
platform_audit = Table(
    "platform_audit",
    metadata,
    Column("audit_id", GUID, primary_key=True),
    Column("hash", Text, nullable=False),
    Column("previous_hash", Text, nullable=False, default="GENESIS"),
    Column("ts", DateTime(timezone=True)),
    Column("actor_email", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("target_type", Text),
    Column("target_id", GUID),
    Column("target_workspace_id", GUID),
    Column("state_before", JSONB_T),
    Column("state_after", JSONB_T),
    Column("ip", INET_T),
    Column("metadata", JSONB_T, default=dict),
)

# Plan catalog (Phase 2). A plan is a bundle of public.workspaces.feature_*
# booleans + soft `limits`. `stripe_*` is a billing-later seam (stays NULL).
# The FK workspace_plans.plan_key -> plans.key is declared in the migration DDL
# (Postgres); it is omitted from the Core table because SQLite (tests) does not
# support cross-attached-database foreign keys.
plans = Table(
    "plans",
    metadata,
    Column("id", GUID, primary_key=True),
    Column("key", Text, unique=True, nullable=False),
    Column("name", Text),
    Column("description", Text),
    Column("feature_flags", JSONB_T, nullable=False, default=dict),
    Column("limits", JSONB_T, nullable=False, default=dict),
    Column("is_public", Boolean, default=True),
    Column("sort_order", Integer, default=0),
    Column("stripe_price_id", Text),
    Column("monthly_price_cents", Integer),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

# Per-workspace plan assignment + one-off overrides. The *effect* of a plan is
# applied to public.workspaces.feature_* by plan_service.apply_plan; this table
# records WHICH plan a workspace is on (operator-owned).
workspace_plans = Table(
    "workspace_plans",
    metadata,
    Column("workspace_id", GUID, primary_key=True),
    Column("plan_key", Text, nullable=False),
    Column("plan_overrides", JSONB_T, nullable=False, default=dict),
    Column("trial_ends_at", DateTime(timezone=True)),
    Column("stripe_customer_id", Text),
    Column("stripe_subscription_id", Text),
    Column("updated_at", DateTime(timezone=True)),
)

# ── Phase 3 — Customer footprints ──────────────────────────────────────────

# Daily per-workspace usage snapshot + engagement score, computed by the admin
# service's own Celery (tasks/footprint_tasks.py) from public.* via the
# service-role session. One row per (workspace_id, day); the rollup is
# idempotent (re-running a day replaces its row). The seat *limit* is NOT stored
# here — it lives on the plan and is joined in at read time so it stays current
# (see footprint_service.list_footprints / the over-limit filter).
customer_footprint_daily = Table(
    "customer_footprint_daily",
    metadata,
    Column("workspace_id", GUID, primary_key=True),
    Column("day", Date, primary_key=True),
    Column("active_users_1d", Integer, nullable=False, default=0),
    Column("active_users_7d", Integer, nullable=False, default=0),
    Column("active_users_30d", Integer, nullable=False, default=0),
    Column("sops_count", Integer, nullable=False, default=0),
    Column("incidents_count", Integer, nullable=False, default=0),
    Column("capas_count", Integer, nullable=False, default=0),
    Column("risks_count", Integer, nullable=False, default=0),
    Column("storage_bytes", BigInteger, nullable=False, default=0),
    Column("seats_used", Integer, nullable=False, default=0),
    Column("last_active_at", DateTime(timezone=True)),
    Column("engagement_score", NUMERIC_T, nullable=False, default=0),
)

# Funnel / source capture. Basic signup counts still derive from
# public.users.created_at (Phase 1 overview); this table records source/UTM/plan
# context per signup so the funnel can be sliced later. The daily
# signup_funnel_rollup task backfills one row per new user (idempotent by
# user_id).
signup_events = Table(
    "signup_events",
    metadata,
    Column("id", GUID, primary_key=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("workspace_id", GUID),
    Column("user_id", GUID),
    Column("source", Text),
    Column("utm", JSONB_T, default=dict),
    Column("plan_at_signup", Text),
)
