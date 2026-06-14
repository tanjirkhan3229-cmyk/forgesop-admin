"""platform: plans + workspace_plans (Phase 2)

Revision ID: 0002_plans
Revises: 0001_platform_schema
Create Date: 2026-06-14

Adds the plan catalog and per-workspace plan assignment to the `platform`
schema. Seeds free/pro/enterprise (from app.services.plan_seeds — the same
source the tests use) and backfills every existing public.workspaces id onto
the `free` plan.

Touches the `platform` schema ONLY. It READS public.workspaces for the
backfill (a read is allowed; we never DDL public). The `stripe_*` columns are a
billing-later seam and are created NULL.
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

from app.services.plan_seeds import PLAN_SEEDS

revision = "0002_plans"
down_revision = "0001_platform_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.plans (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            key                  text UNIQUE NOT NULL,
            name                 text,
            description          text,
            feature_flags        jsonb NOT NULL DEFAULT '{}'::jsonb,
            limits               jsonb NOT NULL DEFAULT '{}'::jsonb,
            is_public            boolean NOT NULL DEFAULT true,
            sort_order           integer NOT NULL DEFAULT 0,
            stripe_price_id      text,
            monthly_price_cents  integer,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE platform.workspace_plans (
            workspace_id           uuid PRIMARY KEY,
            plan_key               text NOT NULL REFERENCES platform.plans(key),
            plan_overrides         jsonb NOT NULL DEFAULT '{}'::jsonb,
            trial_ends_at          timestamptz,
            stripe_customer_id     text,
            stripe_subscription_id text,
            updated_at             timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # Seed the catalog from the shared source of truth.
    for p in PLAN_SEEDS:
        op.execute(
            text(
                """
                INSERT INTO platform.plans
                    (key, name, description, feature_flags, limits,
                     is_public, sort_order, monthly_price_cents)
                VALUES
                    (:key, :name, :description,
                     CAST(:feature_flags AS jsonb), CAST(:limits AS jsonb),
                     :is_public, :sort_order, :monthly_price_cents)
                ON CONFLICT (key) DO NOTHING
                """
            ).bindparams(
                key=p["key"],
                name=p["name"],
                description=p["description"],
                feature_flags=json.dumps(p["feature_flags"]),
                limits=json.dumps(p["limits"]),
                is_public=p["is_public"],
                sort_order=p["sort_order"],
                monthly_price_cents=p["monthly_price_cents"],
            )
        )

    # Backfill: every existing workspace starts on `free`.
    op.execute(
        """
        INSERT INTO platform.workspace_plans (workspace_id, plan_key, plan_overrides, updated_at)
        SELECT id, 'free', '{}'::jsonb, now() FROM public.workspaces
        ON CONFLICT (workspace_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.workspace_plans")
    op.execute("DROP TABLE IF EXISTS platform.plans")
