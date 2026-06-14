"""platform: customer_footprint_daily + signup_events (Phase 3)

Revision ID: 0003_footprints
Revises: 0004_admin_passwords
Create Date: 2026-06-14

Note: re-parented onto 0004_admin_passwords during the Phase 6/7 merge to
linearise two branches that both forked from 0002_plans (this repo's Alembic is
a single linear history). Revision IDs are unchanged; only the order shifted.

Adds the daily per-tenant footprint snapshot and the signup-funnel capture table
to the `platform` schema. Populated by the admin service's own Celery beat
(app/tasks/footprint_tasks.py) from public.* via the service-role connection.

Touches the `platform` schema ONLY (Architecture §5 / §11). It never DDLs
`public`; the rollups only READ public.*. Raw SQL so the production DDL is
exactly the spec. `engagement_score` is numeric(6,2): 0.00–100.00.
"""

from __future__ import annotations

from alembic import op

revision = "0003_footprints"
down_revision = "0004_admin_passwords"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.customer_footprint_daily (
            workspace_id      uuid NOT NULL,
            day               date NOT NULL,
            active_users_1d   integer NOT NULL DEFAULT 0,
            active_users_7d   integer NOT NULL DEFAULT 0,
            active_users_30d  integer NOT NULL DEFAULT 0,
            sops_count        integer NOT NULL DEFAULT 0,
            incidents_count   integer NOT NULL DEFAULT 0,
            capas_count       integer NOT NULL DEFAULT 0,
            risks_count       integer NOT NULL DEFAULT 0,
            storage_bytes     bigint  NOT NULL DEFAULT 0,
            seats_used        integer NOT NULL DEFAULT 0,
            last_active_at    timestamptz,
            engagement_score  numeric(6,2) NOT NULL DEFAULT 0,
            PRIMARY KEY (workspace_id, day)
        )
        """
    )

    # Directory reads the latest snapshot per workspace, ordered by score.
    op.execute(
        "CREATE INDEX ix_customer_footprint_daily_day "
        "ON platform.customer_footprint_daily (day DESC)"
    )
    op.execute(
        "CREATE INDEX ix_customer_footprint_daily_engagement "
        "ON platform.customer_footprint_daily (engagement_score DESC)"
    )

    op.execute(
        """
        CREATE TABLE platform.signup_events (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            ts               timestamptz NOT NULL,
            workspace_id     uuid,
            user_id          uuid,
            source           text,
            utm              jsonb NOT NULL DEFAULT '{}'::jsonb,
            plan_at_signup   text
        )
        """
    )
    # Idempotency guard: one signup event per user (the rollup skips dupes, the
    # index makes a double-insert a hard error rather than silent duplication).
    op.execute(
        "CREATE UNIQUE INDEX ux_signup_events_user_id "
        "ON platform.signup_events (user_id) WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_signup_events_ts ON platform.signup_events (ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.signup_events")
    op.execute("DROP TABLE IF EXISTS platform.customer_footprint_daily")
