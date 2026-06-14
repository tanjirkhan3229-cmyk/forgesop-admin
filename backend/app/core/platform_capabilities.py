"""
Platform capability registry — the canonical list of operator permissions.

This is the OPERATOR-side analogue of sop-hub's
`backend/app/core/capabilities.py`. It is deliberately a SEPARATE registry:
the tenant `user_can(...)` resolver is workspace-scoped (it resolves against
a workspace + `user_capability_grants`) and must NOT be imported here. A
platform operator has no `workspace_id`; authorization is
platform-role → capability.

v1 starter set (Architecture §4.4):
  * PLATFORM_SUPPORT — read everything; impersonate with consent; no writes.
  * PLATFORM_OPS      — support + change plans, toggle flags, suspend/reactivate.
  * PLATFORM_ADMIN    — ops + manage platform_admins and platform settings.

Adding a gated action is a code-only change: add a `PlatformCapability(...)`
here and a `require_platform_capability(<key>)` at the new route. Keep keys
lowercase, dot-segmented, no hyphens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

PLATFORM_ROLES: Final[tuple[str, ...]] = (
    "PLATFORM_SUPPORT",
    "PLATFORM_OPS",
    "PLATFORM_ADMIN",
)

# Numeric rank for "read" defaults; write capabilities are gated explicitly
# in the role→capability map below, never by rank alone.
PLATFORM_ROLE_RANK: Final[dict[str, int]] = {
    "PLATFORM_SUPPORT": 1,
    "PLATFORM_OPS": 2,
    "PLATFORM_ADMIN": 3,
}


@dataclass(frozen=True)
class PlatformCapability:
    """One operator capability."""

    key: str
    """Canonical dot-segmented key (e.g. `workspace.suspend`)."""

    label: str
    """Short English display name (admin SPA is English-only)."""

    description: str
    """One-line explanation of what the capability unlocks."""


PLATFORM_CAPABILITIES: Final[tuple[PlatformCapability, ...]] = (
    PlatformCapability(
        key="tenant.read",
        label="Read tenant data",
        description="List/search workspaces, users, and signups across all tenants.",
    ),
    PlatformCapability(
        key="plan.apply",
        label="Apply plans / toggle flags",
        description="Assign a plan and reconcile public.workspaces.feature_* columns.",
    ),
    PlatformCapability(
        key="plans.manage",
        label="Manage the plan catalog",
        description="Create and edit plans (feature_flags + limits) in platform.plans.",
    ),
    PlatformCapability(
        key="workspace.manage",
        label="Manage a workspace's plan / flags",
        description=(
            "Apply a plan to a workspace and toggle per-flag/limit overrides "
            "(reconciles public.workspaces.feature_*)."
        ),
    ),
    PlatformCapability(
        key="workspace.suspend",
        label="Suspend / reactivate workspace",
        description="Set public.workspaces.status; blocks the tenant at login.",
    ),
    PlatformCapability(
        key="user.manage",
        label="Manage tenant users",
        description="Deactivate / reset / role-change a tenant user.",
    ),
    PlatformCapability(
        key="user.impersonate",
        label="Impersonate tenant user",
        description="Mint a time-boxed tenant session via the Supabase Admin API.",
    ),
    PlatformCapability(
        key="platform_admins.manage",
        label="Manage operators",
        description="Create, edit, and deactivate platform_admins rows.",
    ),
    PlatformCapability(
        key="platform_settings.manage",
        label="Manage platform settings",
        description="Edit alert thresholds and digest recipients.",
    ),
)

PLATFORM_CAPABILITY_INDEX: Final[dict[str, PlatformCapability]] = {
    c.key: c for c in PLATFORM_CAPABILITIES
}

# Role → capability grants. PLATFORM_OPS inherits SUPPORT; PLATFORM_ADMIN
# inherits OPS. Built explicitly (not by rank) so write authority is always
# auditable from this one map.
_SUPPORT = frozenset({"tenant.read", "user.impersonate"})
_OPS = _SUPPORT | {
    "plan.apply",
    "plans.manage",
    "workspace.manage",
    "workspace.suspend",
    "user.manage",
}
_ADMIN = _OPS | {
    "platform_admins.manage",
    "platform_settings.manage",
}

ROLE_CAPABILITIES: Final[dict[str, frozenset]] = {
    "PLATFORM_SUPPORT": _SUPPORT,
    "PLATFORM_OPS": frozenset(_OPS),
    "PLATFORM_ADMIN": frozenset(_ADMIN),
}


def is_known_platform_capability(key: str) -> bool:
    """True iff `key` is in the active operator vocabulary."""
    return key in PLATFORM_CAPABILITY_INDEX


def role_has_capability(role: str, capability: str) -> bool:
    """True iff `role` is granted `capability`."""
    return capability in ROLE_CAPABILITIES.get(role, frozenset())


__all__ = [
    "PLATFORM_ROLES",
    "PLATFORM_ROLE_RANK",
    "PLATFORM_CAPABILITIES",
    "PLATFORM_CAPABILITY_INDEX",
    "ROLE_CAPABILITIES",
    "PlatformCapability",
    "is_known_platform_capability",
    "role_has_capability",
]
