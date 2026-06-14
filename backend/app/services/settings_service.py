"""
Operator settings store (Phase 7).

A thin key→jsonb store over `platform.platform_settings`. It holds the
operator-tunable knobs that drive the alert sweeps and the digest:

  * `alert_thresholds` — when each sweep fires (signup-drop %, over-seat-limit
    on/off, error-rate %, the dedup cooldown).
  * `digest`          — frequency (`daily`/`weekly`) + enabled flag.
  * `recipients`      — the operator emails that alerts and digests go to.

`SETTINGS_DEFAULTS` is the single source of truth for the editable keys and
their shape — imported by the seed migration AND the tests so they never drift.
`get_all` returns defaults deep-merged with whatever is stored, so a freshly
migrated DB and a hand-edited one both round-trip cleanly.

The reserved `_alert_state` key (alert-cooldown bookkeeping) lives in the same
table but is NEVER returned by `get_all` / accepted by `set_values` — it is
internal to `alert_service`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import platform_settings
from app.services.platform_audit import record_platform_event

# Internal, never operator-editable: alert cooldown state.
ALERT_STATE_KEY = "_alert_state"

# The operator-editable settings and their defaults. Adding a knob here (plus a
# field in the SPA Settings page) is all it takes to expose a new control.
SETTINGS_DEFAULTS: dict[str, Any] = {
    "alert_thresholds": {
        # Signup drop: alert when this window's signups fall this % (or more)
        # below the previous equal-length window. `min_baseline` guards against
        # firing on tiny-number noise (the previous window must have ≥ this).
        "signup_drop_pct": 50,
        "signup_window_days": 7,
        "signup_min_baseline": 5,
        # Tenant over seat limit: alert when seats_used > plan max_seats.
        "over_seat_limit_enabled": True,
        # Error-rate spike (Phase 5): alert when error rate ≥ this %. Only
        # evaluated once Phase 5's api_request_metrics table exists.
        "error_rate_pct": 5,
        # Don't re-fire the same alert more often than this.
        "alert_cooldown_hours": 24,
    },
    "digest": {
        "enabled": True,
        "frequency": "weekly",  # 'daily' | 'weekly'
    },
    "recipients": [],  # operator emails for alerts + digests
}

EDITABLE_KEYS = frozenset(SETTINGS_DEFAULTS)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _merge(default: Any, stored: Any) -> Any:
    """Deep-merge `stored` over `default` (dicts merged key-wise; else replace)."""
    if isinstance(default, dict) and isinstance(stored, dict):
        out = dict(default)
        for k, v in stored.items():
            out[k] = _merge(default.get(k), v) if k in default else v
        return out
    return stored if stored is not None else default


async def _read_raw(session: AsyncSession, key: str) -> Any:
    row = (
        await session.execute(
            select(platform_settings.c.value).where(platform_settings.c.key == key)
        )
    ).first()
    return row[0] if row else None


async def get_all(session: AsyncSession) -> dict:
    """All operator-editable settings, defaults deep-merged with stored values."""
    rows = (
        await session.execute(
            select(platform_settings.c.key, platform_settings.c.value).where(
                platform_settings.c.key.in_(EDITABLE_KEYS)
            )
        )
    ).all()
    stored = {k: v for k, v in rows}
    return {key: _merge(SETTINGS_DEFAULTS[key], stored.get(key)) for key in SETTINGS_DEFAULTS}


async def get_thresholds(session: AsyncSession) -> dict:
    return (await get_all(session))["alert_thresholds"]


async def get_recipients(session: AsyncSession) -> list[str]:
    return list((await get_all(session))["recipients"])


async def _upsert(session: AsyncSession, key: str, value: Any) -> None:
    now = _now()
    exists = (
        await session.execute(
            select(platform_settings.c.key).where(platform_settings.c.key == key)
        )
    ).first()
    if exists:
        await session.execute(
            update(platform_settings)
            .where(platform_settings.c.key == key)
            .values(value=value, updated_at=now)
        )
    else:
        await session.execute(
            insert(platform_settings).values(key=key, value=value, updated_at=now)
        )


async def set_values(
    session: AsyncSession, updates: dict, actor, *, ip: str | None = None
) -> dict:
    """Upsert one or more editable settings keys (audited). Ignores unknown and
    reserved keys. Returns the full merged settings afterwards."""
    before = await get_all(session)
    applied = {k: v for k, v in updates.items() if k in EDITABLE_KEYS}
    for key, value in applied.items():
        # Deep-merge partial updates onto the current value so a PATCH of one
        # threshold doesn't wipe its siblings.
        merged = _merge(before[key], value)
        await _upsert(session, key, merged)

    after = await get_all(session)
    await record_platform_event(
        session,
        actor_email=actor.email,
        action="settings.updated",
        target_type="platform_settings",
        target_id=None,
        state_before=before,
        state_after=after,
        ip=ip,
    )
    return after


# ── internal alert-cooldown state (not exposed via the API) ──────────────────


async def get_alert_state(session: AsyncSession) -> dict:
    return dict(await _read_raw(session, ALERT_STATE_KEY) or {})


async def set_alert_state(session: AsyncSession, state: dict) -> None:
    await _upsert(session, ALERT_STATE_KEY, state)
