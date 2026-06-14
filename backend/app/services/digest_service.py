"""
Operator digest (Phase 7).

A periodic (daily/weekly) summary email for operators: top-line KPIs, signups in
the period, workspaces over their seat limit, and the alerts that fired. Building
the digest (`build_digest`) is a pure read — no side effects — and rendering
(`render_digest_html` / `render_digest_text`) is pure string formatting, so the
whole thing renders deterministically for a fixture in tests. `send_digest`
layers on the recipient lookup, the email, and the `digest.sent` audit row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.public_tables import users, workspaces
from app.models.tables import platform_audit
from app.services import alert_service, notifier, settings_service, tenant_directory
from app.services.platform_audit import record_platform_event

DIGEST_ACTOR_EMAIL = "digests@forgesop.platform"

_PERIOD_DAYS = {"daily": 1, "weekly": 7}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _count_since(session: AsyncSession, table, since: datetime) -> int:
    return int(
        (
            await session.execute(
                select(func.count(table.c.id)).where(table.c.created_at >= since)
            )
        ).scalar()
        or 0
    )


async def build_digest(
    session: AsyncSession, *, period: str = "weekly", now: Optional[datetime] = None
) -> dict:
    """Assemble the digest payload for `period` (pure read)."""
    now = now or _now()
    window_days = _PERIOD_DAYS.get(period, 7)
    since = now - timedelta(days=window_days)

    kpis = await tenant_directory.overview_kpis(session)
    new_users = await _count_since(session, users, since)
    new_workspaces = await _count_since(session, workspaces, since)
    over_limit = await alert_service._detect_over_seat_limit(session)

    alert_rows = (
        await session.execute(
            select(
                platform_audit.c.ts,
                platform_audit.c.state_after,
                platform_audit.c.metadata,
            )
            .where(platform_audit.c.action == "alert.fired")
            .where(platform_audit.c.ts >= since)
            .order_by(platform_audit.c.ts.desc())
        )
    ).mappings().all()
    recent_alerts = [
        {
            "ts": a["ts"].isoformat() if hasattr(a["ts"], "isoformat") else str(a["ts"]),
            "title": (a["state_after"] or {}).get("title"),
            "severity": (a["state_after"] or {}).get("severity"),
            "detail": (a["state_after"] or {}).get("detail"),
        }
        for a in alert_rows
    ]

    return {
        "period": period,
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "kpis": kpis,
        "new_users": new_users,
        "new_workspaces": new_workspaces,
        "over_limit": over_limit,
        "over_limit_count": len(over_limit),
        "recent_alerts": recent_alerts,
        "alert_count": len(recent_alerts),
    }


def render_digest_text(digest: dict) -> str:
    k = digest["kpis"]
    lines = [
        f"ForgeSOP operator digest ({digest['period']})",
        f"Generated {digest['generated_at']}",
        "",
        f"New signups in the last {digest['window_days']}d: "
        f"{digest['new_users']} users, {digest['new_workspaces']} workspaces",
        f"Active workspaces: {k['active_workspaces']} / {k['total_workspaces']}",
        f"Total users: {k['total_users']}",
        "",
        f"Workspaces over seat limit: {digest['over_limit_count']}",
    ]
    for o in digest["over_limit"]:
        lines.append(f"  - {o['detail']}")
    lines.append("")
    lines.append(f"Alerts fired in period: {digest['alert_count']}")
    for a in digest["recent_alerts"]:
        lines.append(f"  - [{a['severity']}] {a['title']}")
    return "\n".join(lines)


def render_digest_html(digest: dict) -> str:
    k = digest["kpis"]
    over_items = "".join(f"<li>{o['detail']}</li>" for o in digest["over_limit"])
    alert_items = "".join(
        f"<li><strong>{a['severity']}</strong> — {a['title']}: {a['detail']}</li>"
        for a in digest["recent_alerts"]
    )
    return (
        f"<h1>ForgeSOP operator digest ({digest['period']})</h1>"
        f"<p>Generated {digest['generated_at']}</p>"
        f"<h2>Signups (last {digest['window_days']}d)</h2>"
        f"<p>{digest['new_users']} new users, {digest['new_workspaces']} new workspaces.</p>"
        f"<h2>Platform</h2>"
        f"<ul>"
        f"<li>Active workspaces: {k['active_workspaces']} / {k['total_workspaces']}</li>"
        f"<li>Total users: {k['total_users']}</li>"
        f"</ul>"
        f"<h2>Over seat limit ({digest['over_limit_count']})</h2>"
        f"<ul>{over_items or '<li>None</li>'}</ul>"
        f"<h2>Alerts ({digest['alert_count']})</h2>"
        f"<ul>{alert_items or '<li>None</li>'}</ul>"
    )


async def send_digest(
    session: AsyncSession, *, period: Optional[str] = None, now: Optional[datetime] = None
) -> Optional[dict]:
    """Build, render, email, and audit the digest. Returns the digest, or None
    if digests are disabled. The caller commits."""
    cfg = await settings_service.get_all(session)
    digest_cfg = cfg["digest"]
    if not digest_cfg.get("enabled", True):
        return None

    period = period or digest_cfg.get("frequency", "weekly")
    recipients = list(cfg["recipients"])

    digest = await build_digest(session, period=period, now=now)
    await notifier.send_email(
        recipients,
        subject=f"ForgeSOP operator digest ({period})",
        html=render_digest_html(digest),
        text=render_digest_text(digest),
    )
    await record_platform_event(
        session,
        actor_email=DIGEST_ACTOR_EMAIL,
        action="digest.sent",
        target_type="digest",
        target_id=None,
        metadata={
            "period": period,
            "alert_count": digest["alert_count"],
            "over_limit_count": digest["over_limit_count"],
        },
    )
    return digest
