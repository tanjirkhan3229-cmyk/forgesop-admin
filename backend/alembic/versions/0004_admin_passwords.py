"""platform: admin password columns (local email+password auth)

Revision ID: 0004_admin_passwords
Revises: 0003_platform_settings
Create Date: 2026-06-15

Adds local-auth columns to platform.platform_admins so the console can run
self-contained email+password login (PBKDF2 hashes; console-issued session
tokens) as an alternative to an external IdP. Both columns are nullable —
NULL password_hash means the operator hasn't set a password yet (first-login
set-password flow). Touches the `platform` schema ONLY.
"""

from __future__ import annotations

from alembic import op

revision = "0004_admin_passwords"
down_revision = "0003_platform_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.platform_admins "
        "ADD COLUMN IF NOT EXISTS password_hash text"
    )
    op.execute(
        "ALTER TABLE platform.platform_admins "
        "ADD COLUMN IF NOT EXISTS password_set_at timestamptz"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.platform_admins DROP COLUMN IF EXISTS password_set_at")
    op.execute("ALTER TABLE platform.platform_admins DROP COLUMN IF EXISTS password_hash")
