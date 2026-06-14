"""platform: platform_settings (Phase 7 — alerts & digests)

Revision ID: 0003_platform_settings
Revises: 0002_plans
Create Date: 2026-06-14

Adds the operator-tunable settings store that drives the Phase 7 alert sweeps
and the operator digest: one row per key, value is free-form jsonb. Seeds the
default alert thresholds, digest config, and an empty recipients list (from
app.services.settings_service.SETTINGS_DEFAULTS — the same source the service
and tests use, so the two can never drift).

Touches the `platform` schema ONLY. No `public` DDL.
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

from app.services.settings_service import SETTINGS_DEFAULTS

revision = "0003_platform_settings"
down_revision = "0002_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.platform_settings (
            key         text PRIMARY KEY,
            value       jsonb NOT NULL DEFAULT '{}'::jsonb,
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # Seed the operator-editable defaults. `_alert_state` (internal cooldown
    # bookkeeping) is intentionally NOT seeded — it is created lazily on first
    # sweep and never exposed through the settings API.
    for key, value in SETTINGS_DEFAULTS.items():
        op.execute(
            text(
                """
                INSERT INTO platform.platform_settings (key, value)
                VALUES (:key, CAST(:value AS jsonb))
                ON CONFLICT (key) DO NOTHING
                """
            ).bindparams(key=key, value=json.dumps(value))
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.platform_settings")
