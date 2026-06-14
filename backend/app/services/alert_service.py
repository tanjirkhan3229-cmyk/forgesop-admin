"""
Threshold alert sweeps (Phase 7).

Detection is **pure** (`detect_alerts` → a list of alert dicts, no side effects),
so it is trivially unit-testable against a seeded fixture. Firing
(`run_sweeps`) layers on cooldown dedup, the notification email, and the audit
row — so a tripped condition raises exactly one alert per cooldown window, not
one per sweep tick.

Sweeps:
  * **signup_drop** — this window's signups fell ≥ `signup_drop_pct` below the
    previous equal-length window (guarded by `signup_min_baseline`).
  * **over_seat_limit** — a workspace's seats_used exceeds its plan's
    `max_seats` (per-workspace alert).
  * **error_rate_spike** — Phase 5's `api_request_metrics` is present AND the
    error rate ≥ `error_rate_pct`. Skipped (no alert) until Phase 5 ships the
    table — the sweep degrades gracefully rather than erroring.

Each alert carries a stable `key`; the cooldown state in
`platform_settings._alert_state` is keyed by it, so re-running the sweep while a
condition persists does not re-notify until the cooldown elapses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.public_tables import users, workspaces
from app.models.tables import plans, workspace_plans
from app.services import notifier, settings_service
from app.services.platform_audit import record_platform_event

logger = logging.getLogger(__name__)

# Audit actor for sweep-originated alerts (not a real operator row).
ALERT_ACTOR_EMAIL = "alerts@forgesop.platform"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _alert(
    *, type_: str, key: str, severity: str, title: str, detail: str,
    context: Optional[dict] = None, workspace_id: Optional[str] = None,
) -> dict:
    return {
        "type": type_,
        "key": key,
        "severity": severity,
        "title": title,
        "detail": detail,
        "context": context or {},
        "workspace_id": workspace_id,
    }


async def _table_exists(session: AsyncSession, schema: str, name: str) -> bool:
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        got = await session.execute(text(f"SELECT to_regclass('{schema}.{name}')"))
        return got.scalar() is not None
    if dialect == "sqlite":
        got = await session.execute(
            text(
                f"SELECT name FROM {schema}.sqlite_master "
                "WHERE type='table' AND name=:n"
            ),
            {"n": name},
        )
        return got.first() is not None
    return False


async def _signups_between(
    session: AsyncSession, start: datetime, end: datetime
) -> int:
    stmt = select(func.count(users.c.id)).where(
        users.c.created_at >= start, users.c.created_at < end
    )
    return int((await session.execute(stmt)).scalar() or 0)


# ── individual sweeps (pure) ─────────────────────────────────────────────────


async def _detect_signup_drop(
    session: AsyncSession, thresholds: dict, now: datetime
) -> list[dict]:
    window_days = int(thresholds.get("signup_window_days", 7))
    drop_pct = float(thresholds.get("signup_drop_pct", 50))
    min_baseline = int(thresholds.get("signup_min_baseline", 5))

    window = timedelta(days=window_days)
    recent = await _signups_between(session, now - window, now)
    previous = await _signups_between(session, now - 2 * window, now - window)

    # Need a meaningful baseline to call it a "drop".
    if previous < min_baseline:
        return []
    actual_drop = (previous - recent) / previous * 100
    if actual_drop < drop_pct:
        return []

    return [
        _alert(
            type_="signup_drop",
            key=f"signup_drop:{window_days}d",
            severity="warning",
            title="Signups dropped sharply",
            detail=(
                f"New signups fell {actual_drop:.0f}% over the last {window_days}d "
                f"({previous} → {recent}), at or beyond the {drop_pct:.0f}% threshold."
            ),
            context={
                "window_days": window_days,
                "previous": previous,
                "recent": recent,
                "drop_pct": round(actual_drop, 1),
                "threshold_pct": drop_pct,
            },
        )
    ]


async def _seats_used_by_workspace(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(users.c.workspace_id, func.count(users.c.id))
        .where(users.c.workspace_id.isnot(None))
        .group_by(users.c.workspace_id)
    )
    return {str(ws): int(n) for ws, n in rows.all()}


async def _detect_over_seat_limit(session: AsyncSession) -> list[dict]:
    if not await _table_exists(session, "platform", "workspace_plans"):
        return []

    seats_used = await _seats_used_by_workspace(session)
    names = {
        str(wid): name
        for wid, name in (
            await session.execute(select(workspaces.c.id, workspaces.c.name))
        ).all()
    }

    assignments = (
        await session.execute(
            select(
                workspace_plans.c.workspace_id,
                workspace_plans.c.plan_overrides,
                plans.c.limits,
            ).join(plans, plans.c.key == workspace_plans.c.plan_key)
        )
    ).all()

    alerts: list[dict] = []
    for wid, overrides, limits in assignments:
        wid = str(wid)
        plan_limits = dict(limits or {})
        override_limits = dict((overrides or {}).get("limits") or {})
        max_seats = override_limits.get("max_seats", plan_limits.get("max_seats"))
        if not isinstance(max_seats, int):
            continue
        used = seats_used.get(wid, 0)
        if used <= max_seats:
            continue
        alerts.append(
            _alert(
                type_="over_seat_limit",
                key=f"over_seat_limit:{wid}",
                severity="warning",
                title="Workspace over seat limit",
                detail=(
                    f"{names.get(wid, wid)} is using {used} seats, over its plan "
                    f"limit of {max_seats}."
                ),
                context={"seats_used": used, "max_seats": max_seats},
                workspace_id=wid,
            )
        )
    return alerts


async def _detect_error_rate_spike(
    session: AsyncSession, thresholds: dict, now: datetime
) -> list[dict]:
    # Phase 5 seam: no api_request_metrics table yet ⇒ nothing to evaluate.
    if not await _table_exists(session, "platform", "api_request_metrics"):
        return []

    threshold_pct = float(thresholds.get("error_rate_pct", 5))
    window_start = now - timedelta(hours=1)
    row = (
        await session.execute(
            text(
                "SELECT "
                "  COALESCE(SUM(error_count), 0) AS errors, "
                "  COALESCE(SUM(request_count), 0) AS total "
                "FROM platform.api_request_metrics WHERE bucket_start >= :start"
            ),
            {"start": window_start},
        )
    ).mappings().first()
    total = int(row["total"]) if row else 0
    errors = int(row["errors"]) if row else 0
    if total == 0:
        return []
    rate = errors / total * 100
    if rate < threshold_pct:
        return []
    return [
        _alert(
            type_="error_rate_spike",
            key="error_rate_spike:1h",
            severity="critical",
            title="API error rate spiking",
            detail=f"Error rate is {rate:.1f}% over the last hour (≥ {threshold_pct:.0f}%).",
            context={"error_rate_pct": round(rate, 1), "threshold_pct": threshold_pct},
        )
    ]


# ── orchestration ────────────────────────────────────────────────────────────


async def detect_alerts(
    session: AsyncSession, *, thresholds: dict, now: Optional[datetime] = None
) -> list[dict]:
    """Run every enabled sweep and return the triggered alerts (no side effects)."""
    now = now or _now()
    alerts: list[dict] = []
    alerts += await _detect_signup_drop(session, thresholds, now)
    if thresholds.get("over_seat_limit_enabled", True):
        alerts += await _detect_over_seat_limit(session)
    alerts += await _detect_error_rate_spike(session, thresholds, now)
    return alerts


def _in_cooldown(last_iso: Any, now: datetime, cooldown_hours: float) -> bool:
    if not last_iso:
        return False
    try:
        last = datetime.fromisoformat(str(last_iso))
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) < timedelta(hours=cooldown_hours)


async def run_sweeps(
    session: AsyncSession, *, now: Optional[datetime] = None
) -> list[dict]:
    """Detect, suppress alerts still in cooldown, then notify + audit the rest.

    Returns the alerts that actually fired this run. The caller commits.
    """
    now = now or _now()
    thresholds = await settings_service.get_thresholds(session)
    recipients = await settings_service.get_recipients(session)
    cooldown_hours = float(thresholds.get("alert_cooldown_hours", 24))

    detected = await detect_alerts(session, thresholds=thresholds, now=now)
    state = await settings_service.get_alert_state(session)

    fired: list[dict] = []
    for alert in detected:
        if _in_cooldown(state.get(alert["key"]), now, cooldown_hours):
            continue
        await notifier.send_email(
            recipients,
            subject=f"[ForgeSOP alert] {alert['title']}",
            html=_render_alert_html(alert),
            text=alert["detail"],
        )
        await record_platform_event(
            session,
            actor_email=ALERT_ACTOR_EMAIL,
            action="alert.fired",
            target_type="alert",
            target_id=None,
            target_workspace_id=alert.get("workspace_id"),
            state_after=alert,
            metadata={"alert_type": alert["type"], "severity": alert["severity"]},
        )
        state[alert["key"]] = now.isoformat()
        fired.append(alert)

    if fired:
        await settings_service.set_alert_state(session, state)
    return fired


def _render_alert_html(alert: dict) -> str:
    return (
        f"<h2>{alert['title']}</h2>"
        f"<p><strong>{alert['severity'].upper()}</strong> — {alert['detail']}</p>"
    )
