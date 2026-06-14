"""
Cross-tenant reads of `public.*` via the service-role session.

This is the ONLY place in the codebase where cross-tenant SELECTs live
(Architecture §4.5: "All cross-tenant SQL lives in the admin service's
services/ layer and is reviewed as deliberate"). Endpoints call these
functions; they never build cross-tenant queries inline.

Everything here is READ-ONLY. The service-role bypasses RLS, so these
functions see every workspace — keep them auditable and narrow.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.public_tables import audit_trail, users, workspaces

# Supported signup ranges → number of daily buckets (inclusive of today).
_RANGE_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
DEFAULT_RANGE = "30d"

_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 25


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _full_name(first: Optional[str], last: Optional[str]) -> Optional[str]:
    name = " ".join(p for p in (first, last) if p)
    return name or None


def _iso(value: Any) -> Optional[str]:
    """ISO string for a timestamp that may be a datetime (typed query) or a raw
    string (raw `SELECT *`, which bypasses the column type on SQLite)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # SQLite may hand back an ISO string for a DATETIME column.
    return datetime.fromisoformat(str(value)).date()


async def _workspace_plans_exists(session: AsyncSession) -> bool:
    """Has Phase 2's `platform.workspace_plans` shipped yet?

    Phase 1 must degrade `plan` to null when the table does not exist. The
    check is dialect-aware so it works against both Postgres and the SQLite
    test DB (where `platform` is an ATTACH-ed database).
    """
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


async def _plan_by_workspace(session: AsyncSession) -> dict[str, Optional[str]]:
    """{workspace_id: plan_key} from platform.workspace_plans, or {} if absent."""
    if not await _workspace_plans_exists(session):
        return {}
    rows = await session.execute(
        text("SELECT workspace_id, plan_key FROM platform.workspace_plans")
    )
    return {str(r[0]): r[1] for r in rows.all()}


# ── Overview ──────────────────────────────────────────────────────────────


async def overview_kpis(session: AsyncSession) -> dict:
    """Top-line KPIs: signups over 24h/7d/30d, active workspaces, total users."""
    now = _now()

    async def _signups_since(delta: timedelta) -> int:
        stmt = select(func.count(users.c.id)).where(users.c.created_at >= now - delta)
        return int((await session.execute(stmt)).scalar() or 0)

    total_users = int(
        (await session.execute(select(func.count(users.c.id)))).scalar() or 0
    )
    total_workspaces = int(
        (await session.execute(select(func.count(workspaces.c.id)))).scalar() or 0
    )
    active_workspaces = int(
        (
            await session.execute(
                select(func.count(workspaces.c.id)).where(
                    func.coalesce(workspaces.c.is_suspended, False) == False  # noqa: E712
                )
            )
        ).scalar()
        or 0
    )

    return {
        "signups": {
            "last_24h": await _signups_since(timedelta(hours=24)),
            "last_7d": await _signups_since(timedelta(days=7)),
            "last_30d": await _signups_since(timedelta(days=30)),
        },
        "active_workspaces": active_workspaces,
        "total_workspaces": total_workspaces,
        "total_users": total_users,
    }


# ── Signups ─────────────────────────────────────────────────────────────────


async def signup_series(session: AsyncSession, range_: str = DEFAULT_RANGE) -> dict:
    """Daily new-user and new-workspace counts over the requested range.

    Returns zero-filled daily buckets so the chart has no gaps. Bucketing is
    done in Python (by UTC calendar day) to stay portable across Postgres and
    the SQLite test DB.
    """
    days = _RANGE_DAYS.get(range_, _RANGE_DAYS[DEFAULT_RANGE])
    today = _now().date()
    start_date = today - timedelta(days=days - 1)
    cutoff = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)

    user_rows = await session.execute(
        select(users.c.created_at).where(users.c.created_at >= cutoff)
    )
    ws_rows = await session.execute(
        select(workspaces.c.created_at).where(workspaces.c.created_at >= cutoff)
    )

    user_counts: dict[date, int] = {}
    for (created_at,) in user_rows.all():
        d = _as_date(created_at)
        if d is not None:
            user_counts[d] = user_counts.get(d, 0) + 1

    ws_counts: dict[date, int] = {}
    for (created_at,) in ws_rows.all():
        d = _as_date(created_at)
        if d is not None:
            ws_counts[d] = ws_counts.get(d, 0) + 1

    series = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        series.append(
            {
                "date": d.isoformat(),
                "users": user_counts.get(d, 0),
                "workspaces": ws_counts.get(d, 0),
            }
        )

    return {
        "range": range_ if range_ in _RANGE_DAYS else DEFAULT_RANGE,
        "series": series,
        "totals": {
            "users": sum(p["users"] for p in series),
            "workspaces": sum(p["workspaces"] for p in series),
        },
    }


# ── Workspaces ────────────────────────────────────────────────────────────


async def list_workspaces(
    session: AsyncSession,
    *,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict:
    """Paginated workspace directory with member count, last activity, plan."""
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

    where = []
    if search:
        where.append(workspaces.c.name.ilike(f"%{search}%"))

    total = int(
        (
            await session.execute(
                select(func.count(workspaces.c.id)).where(*where)
            )
        ).scalar()
        or 0
    )

    # member_count + last_activity from a LEFT JOIN on users.
    j = workspaces.outerjoin(users, users.c.workspace_id == workspaces.c.id)
    stmt = (
        select(
            workspaces.c.id,
            workspaces.c.name,
            workspaces.c.slug,
            workspaces.c.is_suspended,
            workspaces.c.created_at,
            func.count(users.c.id).label("member_count"),
            func.max(users.c.last_active_at).label("last_activity"),
        )
        .select_from(j)
        .where(*where)
        .group_by(
            workspaces.c.id,
            workspaces.c.name,
            workspaces.c.slug,
            workspaces.c.is_suspended,
            workspaces.c.created_at,
        )
        .order_by(workspaces.c.created_at.desc().nullslast())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    rows = (await session.execute(stmt)).mappings().all()
    plans = await _plan_by_workspace(session)

    items = [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "slug": r["slug"],
            "is_suspended": bool(r["is_suspended"]) if r["is_suspended"] is not None else False,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "member_count": int(r["member_count"] or 0),
            "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None,
            "plan": plans.get(str(r["id"])),
        }
        for r in rows
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_workspace(session: AsyncSession, workspace_id: str) -> Optional[dict]:
    """Detail: base fields + feature_* flags + members + recent audit_trail."""
    # SELECT * to pick up whatever feature_* columns the live table carries.
    row = (
        await session.execute(
            text("SELECT * FROM public.workspaces WHERE id = :wid"),
            {"wid": workspace_id},
        )
    ).mappings().first()
    if row is None:
        return None

    feature_flags = {
        k: bool(v) for k, v in row.items() if k.startswith("feature_")
    }

    member_rows = (
        await session.execute(
            select(
                users.c.id,
                users.c.email,
                users.c.first_name,
                users.c.last_name,
                users.c.role,
                users.c.status,
                users.c.last_active_at,
            )
            .where(users.c.workspace_id == workspace_id)
            .order_by(users.c.last_active_at.desc().nullslast())
        )
    ).mappings().all()

    members = [
        {
            "id": str(m["id"]),
            "email": m["email"],
            "name": _full_name(m["first_name"], m["last_name"]),
            "role": m["role"],
            "status": m["status"],
            "last_active_at": m["last_active_at"].isoformat() if m["last_active_at"] else None,
        }
        for m in member_rows
    ]

    audit_rows = (
        await session.execute(
            select(
                audit_trail.c.audit_id,
                audit_trail.c.timestamp,
                audit_trail.c.event_type,
                audit_trail.c.action,
                audit_trail.c.actor_email,
                audit_trail.c.actor_name,
            )
            .where(audit_trail.c.organization_id == workspace_id)
            .order_by(audit_trail.c.timestamp.desc())
            .limit(20)
        )
    ).mappings().all()

    recent_activity = [
        {
            "audit_id": str(a["audit_id"]),
            "timestamp": a["timestamp"].isoformat() if a["timestamp"] else None,
            "event_type": a["event_type"],
            "action": a["action"],
            "actor_email": a["actor_email"],
            "actor_name": a["actor_name"],
        }
        for a in audit_rows
    ]

    plans = await _plan_by_workspace(session)

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "slug": row.get("slug"),
        "is_suspended": bool(row.get("is_suspended")) if row.get("is_suspended") is not None else False,
        "created_at": _iso(row.get("created_at")),
        "plan": plans.get(str(row["id"])),
        "feature_flags": feature_flags,
        "member_count": len(members),
        "members": members,
        "recent_activity": recent_activity,
    }


# ── Users ─────────────────────────────────────────────────────────────────


async def list_users(
    session: AsyncSession,
    *,
    search: Optional[str] = None,
    workspace_id: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict:
    """Cross-tenant user directory with the owning workspace name joined in."""
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

    where = []
    if search:
        like = f"%{search}%"
        where.append(
            func.coalesce(users.c.email, "").ilike(like)
            | func.coalesce(users.c.first_name, "").ilike(like)
            | func.coalesce(users.c.last_name, "").ilike(like)
        )
    if workspace_id:
        where.append(users.c.workspace_id == workspace_id)
    if role:
        where.append(users.c.role == role)
    if status:
        where.append(users.c.status == status)

    cond = and_(*where) if where else None

    count_stmt = select(func.count(users.c.id))
    if cond is not None:
        count_stmt = count_stmt.where(cond)
    total = int((await session.execute(count_stmt)).scalar() or 0)

    j = users.outerjoin(workspaces, workspaces.c.id == users.c.workspace_id)
    stmt = (
        select(
            users.c.id,
            users.c.email,
            users.c.first_name,
            users.c.last_name,
            users.c.role,
            users.c.status,
            users.c.workspace_id,
            workspaces.c.name.label("workspace_name"),
            users.c.last_active_at,
            users.c.created_at,
        )
        .select_from(j)
        .order_by(users.c.created_at.desc().nullslast())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    if cond is not None:
        stmt = stmt.where(cond)

    rows = (await session.execute(stmt)).mappings().all()
    items = [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "name": _full_name(r["first_name"], r["last_name"]),
            "role": r["role"],
            "status": r["status"],
            "workspace_id": str(r["workspace_id"]) if r["workspace_id"] else None,
            "workspace_name": r["workspace_name"],
            "last_active_at": r["last_active_at"].isoformat() if r["last_active_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}
