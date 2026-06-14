"""
Footprint service — daily per-tenant usage + engagement, computed from public.*.

This is a cross-tenant READ service (Architecture §4.5 / §7): it reads
`public.audit_trail`, `public.users`, and the module tables across all tenants
via the service-role session and writes ONLY to `platform.customer_footprint_daily`
and `platform.signup_events` (operator-owned). It never writes a tenant table.

Two jobs run on the admin service's own Celery beat (tasks/footprint_tasks.py):
  * `customer_footprint_rollup` — one snapshot row per workspace per day;
  * `signup_funnel_rollup`      — backfills signup_events from public.users.

The directory endpoints (api/v1/footprints.py) read the latest snapshot per
workspace and join the plan's seat limit in at read time (so "over seat limit"
always reflects the current plan, not the limit at snapshot time).

Engagement score (compute_engagement_score) is a PURE, DETERMINISTIC function of
its inputs — see its docstring for the exact weighting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.public_tables import (
    audit_trail,
    capas,
    document_versions,
    ehs_incidents,
    risks,
    sops,
    users,
    workspaces,
)
from app.models.tables import (
    customer_footprint_daily,
    plans,
    signup_events,
    workspace_plans,
)

# The four adoption modules whose object counts feed "module breadth".
_MODULE_COUNT_KEYS = ("sops_count", "incidents_count", "capas_count", "risks_count")

# Engagement score weights (sum to 1.0). Documented in compute_engagement_score.
_W_RECENCY = 0.5
_W_BREADTH = 0.3
_W_SEAT_UTIL = 0.2
# Recency decays linearly to 0 over this many days since last activity.
_RECENCY_HORIZON_DAYS = 30


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _as_datetime(value: Any) -> Optional[datetime]:
    """Coerce a stored timestamp (datetime on PG, ISO string on SQLite) to an
    aware datetime; None passes through."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    dt = _as_datetime(value)
    return dt.date() if dt else None


def _iso(value: Any) -> Optional[str]:
    dt = _as_datetime(value)
    return dt.isoformat() if dt else None


# ── Engagement score ────────────────────────────────────────────────────────


def compute_engagement_score(
    *,
    day: date,
    last_active_at: Any,
    module_counts: dict[str, int],
    seats_used: int,
    seat_limit: Optional[int],
) -> float:
    """A 0–100 engagement score — a weighted blend of three sub-scores, each
    normalised to [0, 1] (Architecture §7: "recency + module breadth + seat
    utilization"). PURE and DETERMINISTIC: identical inputs always yield the
    identical score (no clock reads — recency is measured against `day`).

    Sub-scores
    ----------
    recency (weight 0.50)
        How recently anyone in the workspace was active, measured from `day`.
        `days_inactive = max(0, day - last_active_at.date())`; the score decays
        linearly from 1.0 (active on `day`) to 0.0 at `_RECENCY_HORIZON_DAYS`
        (30d) and stays 0 beyond. No activity on record → 0.
    module breadth (weight 0.30)
        Fraction of the four adoption modules (SOPs, incidents, CAPAs, risks)
        that have at least one object: `active_modules / 4`. Rewards breadth of
        adoption, not raw volume.
    seat utilization (weight 0.20)
        `min(1, seats_used / seat_limit)` when a positive seat limit exists,
        else 0 (an unlimited/unknown plan contributes no utilization signal).

    Returns `round(100 * weighted_sum, 2)`.
    """
    # recency
    last_dt = _as_datetime(last_active_at)
    if last_dt is None:
        recency = 0.0
    else:
        days_inactive = max(0, (day - last_dt.date()).days)
        recency = max(0.0, 1.0 - days_inactive / _RECENCY_HORIZON_DAYS)

    # module breadth
    active_modules = sum(1 for k in _MODULE_COUNT_KEYS if (module_counts.get(k) or 0) > 0)
    breadth = active_modules / len(_MODULE_COUNT_KEYS)

    # seat utilization
    if seat_limit and seat_limit > 0:
        seat_util = min(1.0, seats_used / seat_limit)
    else:
        seat_util = 0.0

    score = 100.0 * (_W_RECENCY * recency + _W_BREADTH * breadth + _W_SEAT_UTIL * seat_util)
    return round(score, 2)


# ── Plan seat limits (joined in at read & compute time) ─────────────────────


async def _plan_tables_exist(session: AsyncSession) -> bool:
    """Have the Phase-2 plan tables shipped? (Dialect-aware, like
    tenant_directory._workspace_plans_exists.)"""
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        got = await session.execute(text("SELECT to_regclass('platform.workspace_plans')"))
        return got.scalar() is not None
    if dialect == "sqlite":
        got = await session.execute(
            text(
                "SELECT name FROM platform.sqlite_master "
                "WHERE type='table' AND name='workspace_plans'"
            )
        )
        return got.first() is not None
    return False


async def effective_seat_limits(session: AsyncSession) -> dict[str, Optional[int]]:
    """`{workspace_id: max_seats or None}` — the EFFECTIVE seat limit per
    workspace: a `plan_overrides.limits.max_seats` override wins over the plan's
    `limits.max_seats`. Returns {} when the plan tables are absent."""
    if not await _plan_tables_exist(session):
        return {}

    plan_limits: dict[str, Optional[int]] = {}
    for row in (await session.execute(select(plans.c.key, plans.c.limits))).all():
        limits = row[1] or {}
        plan_limits[row[0]] = limits.get("max_seats")

    out: dict[str, Optional[int]] = {}
    rows = await session.execute(
        select(
            workspace_plans.c.workspace_id,
            workspace_plans.c.plan_key,
            workspace_plans.c.plan_overrides,
        )
    )
    for ws_id, plan_key, overrides in rows.all():
        limit = plan_limits.get(plan_key)
        ov = (overrides or {}).get("limits") or {}
        if "max_seats" in ov:
            limit = ov["max_seats"]
        out[str(ws_id)] = limit
    return out


# ── compute_footprint (one workspace, one day) ──────────────────────────────


async def compute_footprint(
    session: AsyncSession,
    workspace_id: str,
    day: date,
    *,
    seat_limit: Optional[int] = None,
    seat_limit_resolved: bool = False,
) -> dict:
    """Compute (do NOT persist) the footprint row for `workspace_id` on `day`.

    Active-user windows are anchored on `day`: the upper bound is the end of
    `day` (exclusive) and each window reaches back 1 / 7 / 30 days. `seats_used`
    counts ACTIVE tenant users. Storage is the sum of `document_versions.size_bytes`.

    `seat_limit` is used only for the engagement score's seat-utilization term;
    pass it (with `seat_limit_resolved=True`, even if None) to avoid a per-row
    plan lookup inside the rollup loop. If not provided, it is resolved here.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    async def _active_users(window_days: int) -> int:
        lower = day_end - timedelta(days=window_days)
        stmt = select(func.count(func.distinct(audit_trail.c.actor_id))).where(
            and_(
                audit_trail.c.organization_id == workspace_id,
                audit_trail.c.actor_id.isnot(None),
                audit_trail.c.timestamp >= lower,
                audit_trail.c.timestamp < day_end,
            )
        )
        return int((await session.execute(stmt)).scalar() or 0)

    async def _count(table) -> int:
        stmt = select(func.count(table.c.id)).where(table.c.workspace_id == workspace_id)
        return int((await session.execute(stmt)).scalar() or 0)

    active_1d = await _active_users(1)
    active_7d = await _active_users(7)
    active_30d = await _active_users(30)

    sops_count = await _count(sops)
    incidents_count = await _count(ehs_incidents)
    capas_count = await _count(capas)
    risks_count = await _count(risks)

    storage_bytes = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(document_versions.c.size_bytes), 0)).where(
                    document_versions.c.workspace_id == workspace_id
                )
            )
        ).scalar()
        or 0
    )

    seats_used = int(
        (
            await session.execute(
                select(func.count(users.c.id)).where(
                    and_(users.c.workspace_id == workspace_id, users.c.status == "ACTIVE")
                )
            )
        ).scalar()
        or 0
    )

    last_active_at = (
        await session.execute(
            select(func.max(users.c.last_active_at)).where(
                users.c.workspace_id == workspace_id
            )
        )
    ).scalar()

    if not seat_limit_resolved:
        limits = await effective_seat_limits(session)
        seat_limit = limits.get(str(workspace_id))

    module_counts = {
        "sops_count": sops_count,
        "incidents_count": incidents_count,
        "capas_count": capas_count,
        "risks_count": risks_count,
    }
    engagement_score = compute_engagement_score(
        day=day,
        last_active_at=last_active_at,
        module_counts=module_counts,
        seats_used=seats_used,
        seat_limit=seat_limit,
    )

    return {
        "workspace_id": str(workspace_id),
        "day": day,
        "active_users_1d": active_1d,
        "active_users_7d": active_7d,
        "active_users_30d": active_30d,
        "sops_count": sops_count,
        "incidents_count": incidents_count,
        "capas_count": capas_count,
        "risks_count": risks_count,
        "storage_bytes": storage_bytes,
        "seats_used": seats_used,
        "last_active_at": last_active_at,
        "engagement_score": engagement_score,
    }


# ── Rollups (Celery beat) ────────────────────────────────────────────────────


async def run_footprint_rollup(session: AsyncSession, day: date) -> int:
    """Compute + upsert one footprint row for EVERY workspace on `day`.

    Idempotent: a (workspace_id, day) row is deleted then re-inserted, so
    re-running a day never duplicates. Does NOT commit — the caller owns the
    transaction. Returns the number of rows written.
    """
    ws_rows = (await session.execute(select(workspaces.c.id))).all()
    seat_limits = await effective_seat_limits(session)

    written = 0
    for (ws_id,) in ws_rows:
        ws_id = str(ws_id)
        row = await compute_footprint(
            session,
            ws_id,
            day,
            seat_limit=seat_limits.get(ws_id),
            seat_limit_resolved=True,
        )
        await session.execute(
            delete(customer_footprint_daily).where(
                and_(
                    customer_footprint_daily.c.workspace_id == ws_id,
                    customer_footprint_daily.c.day == day,
                )
            )
        )
        await session.execute(insert(customer_footprint_daily).values(**row))
        written += 1

    return written


async def run_signup_funnel_rollup(
    session: AsyncSession, day: Optional[date] = None
) -> int:
    """Backfill `platform.signup_events` from `public.users.created_at`.

    Basic signup counts still derive from `public.users` (Phase 1); this records
    source/UTM/plan context per signup so the funnel can be sliced later. One
    row per user, idempotent by `user_id` (users already recorded are skipped).
    When `day` is given, only users created on that UTC day are considered.
    Does NOT commit. Returns the number of new events inserted.
    """
    existing = {
        str(r[0])
        for r in (
            await session.execute(
                select(signup_events.c.user_id).where(signup_events.c.user_id.isnot(None))
            )
        ).all()
    }

    stmt = select(
        users.c.id, users.c.workspace_id, users.c.created_at
    ).where(users.c.created_at.isnot(None))
    if day is not None:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        stmt = stmt.where(
            and_(users.c.created_at >= day_start, users.c.created_at < day_start + timedelta(days=1))
        )

    plan_by_ws: dict[str, Optional[str]] = {}
    if await _plan_tables_exist(session):
        for r in (
            await session.execute(
                select(workspace_plans.c.workspace_id, workspace_plans.c.plan_key)
            )
        ).all():
            plan_by_ws[str(r[0])] = r[1]

    inserted = 0
    for user_id, ws_id, created_at in (await session.execute(stmt)).all():
        if str(user_id) in existing:
            continue
        await session.execute(
            insert(signup_events).values(
                id=str(uuid.uuid4()),
                ts=created_at,
                workspace_id=str(ws_id) if ws_id is not None else None,
                user_id=str(user_id),
                source="backfill",
                utm={},
                plan_at_signup=plan_by_ws.get(str(ws_id)) if ws_id is not None else None,
            )
        )
        inserted += 1

    return inserted


# ── Directory + detail (read endpoints) ─────────────────────────────────────

_SORT_KEYS = {
    "engagement_score",
    "seats_used",
    "active_users_1d",
    "active_users_7d",
    "active_users_30d",
    "sops_count",
    "incidents_count",
    "capas_count",
    "risks_count",
    "storage_bytes",
    "last_active_at",
    "name",
    "day",
}
DEFAULT_SORT = "engagement_score"
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 25


async def _latest_footprints(session: AsyncSession) -> list[dict]:
    """The most-recent snapshot row per workspace (one row each)."""
    cfd = customer_footprint_daily
    latest = (
        select(cfd.c.workspace_id, func.max(cfd.c.day).label("max_day"))
        .group_by(cfd.c.workspace_id)
        .subquery()
    )
    stmt = select(cfd).join(
        latest,
        and_(cfd.c.workspace_id == latest.c.workspace_id, cfd.c.day == latest.c.max_day),
    )
    return [dict(r) for r in (await session.execute(stmt)).mappings().all()]


def _days_inactive(last_active_at: Any, now: datetime) -> Optional[int]:
    dt = _as_datetime(last_active_at)
    if dt is None:
        return None
    return max(0, (now - dt).days)


def _enrich(row: dict, name: Optional[str], seat_limit: Optional[int], now: datetime) -> dict:
    seats_used = int(row["seats_used"] or 0)
    over_limit = bool(seat_limit and seat_limit > 0 and seats_used > seat_limit)
    di = _days_inactive(row["last_active_at"], now)
    return {
        "workspace_id": str(row["workspace_id"]),
        "name": name,
        "day": row["day"].isoformat() if hasattr(row["day"], "isoformat") else str(row["day"]),
        "active_users_1d": int(row["active_users_1d"] or 0),
        "active_users_7d": int(row["active_users_7d"] or 0),
        "active_users_30d": int(row["active_users_30d"] or 0),
        "sops_count": int(row["sops_count"] or 0),
        "incidents_count": int(row["incidents_count"] or 0),
        "capas_count": int(row["capas_count"] or 0),
        "risks_count": int(row["risks_count"] or 0),
        "storage_bytes": int(row["storage_bytes"] or 0),
        "seats_used": seats_used,
        "seat_limit": seat_limit,
        "over_seat_limit": over_limit,
        "last_active_at": _iso(row["last_active_at"]),
        "days_inactive": di,
        "engagement_score": float(row["engagement_score"] or 0),
    }


async def list_footprints(
    session: AsyncSession,
    *,
    search: Optional[str] = None,
    over_seat_limit: bool = False,
    inactive_days: Optional[int] = None,
    sort: str = DEFAULT_SORT,
    order: str = "desc",
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict:
    """Sortable footprint directory (latest snapshot per workspace) with the
    "over seat limit" and "inactive >= N days" filter chips.

    The seat *limit* is joined from the current plan at read time, so the
    over-limit filter reflects the live plan, not the snapshot. The inactive
    filter selects workspaces with NO recorded activity or whose last activity
    is at least `inactive_days` days ago.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    sort = sort if sort in _SORT_KEYS else DEFAULT_SORT
    descending = order.lower() != "asc"
    now = _now()

    rows = await _latest_footprints(session)
    names = {
        str(r[0]): r[1]
        for r in (
            await session.execute(select(workspaces.c.id, workspaces.c.name))
        ).all()
    }
    seat_limits = await effective_seat_limits(session)

    items = [
        _enrich(r, names.get(str(r["workspace_id"])), seat_limits.get(str(r["workspace_id"])), now)
        for r in rows
    ]

    if search:
        needle = search.lower()
        items = [i for i in items if i["name"] and needle in i["name"].lower()]
    if over_seat_limit:
        items = [i for i in items if i["over_seat_limit"]]
    if inactive_days is not None:
        items = [
            i
            for i in items
            if i["days_inactive"] is None or i["days_inactive"] >= inactive_days
        ]

    def _primary(i: dict):
        if sort == "name":
            return (i["name"] is None, (i["name"] or "").lower())
        v = i.get(sort)
        # None sorts last regardless of direction.
        return (v is None, v if v is not None else 0)

    # workspace_id is a deterministic tiebreaker so equal primary keys (e.g.
    # identical engagement scores) always order the same way across runs.
    items.sort(key=lambda i: (_primary(i), i["workspace_id"]), reverse=descending)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort,
        "order": "desc" if descending else "asc",
    }


async def get_footprint_detail(
    session: AsyncSession, workspace_id: str
) -> Optional[dict]:
    """Per-tenant footprint detail: latest snapshot + a daily trend series.

    Returns None when the workspace does not exist. `trend` is the trailing
    `FOOTPRINT_TREND_DAYS` snapshots (oldest→newest) for the usage chart.
    """
    ws = (
        await session.execute(
            select(workspaces.c.id, workspaces.c.name, workspaces.c.slug).where(
                workspaces.c.id == workspace_id
            )
        )
    ).mappings().first()
    if ws is None:
        return None

    now = _now()
    seat_limits = await effective_seat_limits(session)
    seat_limit = seat_limits.get(str(workspace_id))

    cfd = customer_footprint_daily
    trend_rows = (
        await session.execute(
            select(cfd)
            .where(cfd.c.workspace_id == workspace_id)
            .order_by(cfd.c.day.desc())
            .limit(settings.FOOTPRINT_TREND_DAYS)
        )
    ).mappings().all()
    trend_rows = list(reversed([dict(r) for r in trend_rows]))  # oldest → newest

    trend = [
        {
            "day": r["day"].isoformat() if hasattr(r["day"], "isoformat") else str(r["day"]),
            "active_users_1d": int(r["active_users_1d"] or 0),
            "active_users_7d": int(r["active_users_7d"] or 0),
            "active_users_30d": int(r["active_users_30d"] or 0),
            "sops_count": int(r["sops_count"] or 0),
            "incidents_count": int(r["incidents_count"] or 0),
            "capas_count": int(r["capas_count"] or 0),
            "risks_count": int(r["risks_count"] or 0),
            "storage_bytes": int(r["storage_bytes"] or 0),
            "seats_used": int(r["seats_used"] or 0),
            "engagement_score": float(r["engagement_score"] or 0),
        }
        for r in trend_rows
    ]

    latest = (
        _enrich(trend_rows[-1], ws["name"], seat_limit, now) if trend_rows else None
    )

    return {
        "workspace_id": str(ws["id"]),
        "name": ws["name"],
        "slug": ws["slug"],
        "seat_limit": seat_limit,
        "latest": latest,
        "trend": trend,
    }
