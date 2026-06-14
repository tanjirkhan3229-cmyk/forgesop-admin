"""platform schema: platform_admins + platform_audit

Revision ID: 0001_platform_schema
Revises:
Create Date: 2026-06-14

Phase 0 — stand up the operator allowlist and the hash-chained audit log.
This migration touches the `platform` schema ONLY (Architecture §5). It must
never reference `public`. Raw SQL so the production DDL is exactly the spec.
"""

from __future__ import annotations

from alembic import op

revision = "0001_platform_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS "platform"')

    op.execute(
        """
        CREATE TABLE platform.platform_admins (
            id          uuid PRIMARY KEY,
            email       text UNIQUE NOT NULL,
            role        text NOT NULL
                          CHECK (role IN ('PLATFORM_SUPPORT',
                                          'PLATFORM_OPS',
                                          'PLATFORM_ADMIN')),
            is_active   boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE platform.platform_audit (
            audit_id             uuid PRIMARY KEY,
            hash                 text NOT NULL,
            previous_hash        text NOT NULL DEFAULT 'GENESIS',
            ts                   timestamptz NOT NULL DEFAULT now(),
            actor_email          text NOT NULL,
            action               text NOT NULL,
            target_type          text,
            target_id            uuid,
            target_workspace_id  uuid,
            state_before         jsonb,
            state_after          jsonb,
            ip                   inet,
            metadata             jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )

    # The chain is walked by previous_hash pointer; index the link + a
    # uniqueness guard so a fork (two rows sharing previous_hash) is hard to
    # introduce accidentally.
    op.execute(
        "CREATE INDEX ix_platform_audit_previous_hash "
        "ON platform.platform_audit (previous_hash)"
    )
    op.execute(
        "CREATE INDEX ix_platform_audit_ts ON platform.platform_audit (ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.platform_audit")
    op.execute("DROP TABLE IF EXISTS platform.platform_admins")
    # Leave the schema in place; dropping it is a deliberate operator action.
