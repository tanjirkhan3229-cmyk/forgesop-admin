"""
Plan service — the ONLY path that writes public.workspaces.feature_*.

A plan is a bundle of `public.workspaces.feature_*` booleans + soft `limits`;
there is no billing. `apply_plan` upserts the workspace's plan assignment AND
reconciles the feature_* columns from the plan's `feature_flags`, all in the
caller's transaction (the endpoint commits once → atomic), and audits it via a
`plan.changed` event. `set_overrides` grants/revokes a single flag (or limit)
without changing the plan_key.

Reconciliation writes ONLY the feature_* columns a plan/override explicitly
lists. Columns a plan does not mention are left untouched, so applying `pro`
never silently disables an enterprise-only flag. Column names are validated
against the live `public.workspaces` columns (introspected) before being used
in SQL, so the dynamic UPDATE is safe.

Billing seam: `plans.stripe_price_id` IS settable here (Phase 6) so the Stripe
webhook can map a subscribed price → plan. It is purely a lookup key and does
NOT affect reconciliation — `apply_plan` still writes only the feature_* columns
a plan lists. The `workspace_plans.stripe_*` columns are populated exclusively by
`billing_service` (from webhook events), never here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import plans, workspace_plans
from app.services.platform_audit import record_platform_event


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _dialect(session: AsyncSession) -> str:
    return session.bind.dialect.name


async def _feature_columns(session: AsyncSession) -> list[str]:
    """The live `public.workspaces.feature_*` column names (introspected).

    Dialect-aware so it works against Postgres and the SQLite test DB. The
    result is the whitelist of columns reconciliation is allowed to write.
    """
    dialect = _dialect(session)
    if dialect == "postgresql":
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'workspaces' "
                "AND column_name LIKE 'feature\\_%' ESCAPE '\\'"
            )
        )
        return sorted(r[0] for r in rows.all())
    if dialect == "sqlite":
        rows = await session.execute(text("PRAGMA public.table_info(workspaces)"))
        return sorted(r[1] for r in rows.all() if str(r[1]).startswith("feature_"))
    return []


async def _read_features(
    session: AsyncSession, workspace_id: str, cols: list[str]
) -> dict[str, bool]:
    if not cols:
        return {}
    col_list = ", ".join(cols)  # cols are introspected identifiers (safe)
    row = (
        await session.execute(
            text(f"SELECT {col_list} FROM public.workspaces WHERE id = :wid"),
            {"wid": workspace_id},
        )
    ).mappings().first()
    if row is None:
        return {}
    return {c: bool(row[c]) for c in cols}


async def _reconcile_features(
    session: AsyncSession,
    workspace_id: str,
    feature_flags: dict[str, Any],
    cols: list[str],
) -> None:
    """Set exactly the feature_* columns listed in `feature_flags` (and that
    actually exist on the table). Columns not listed are left untouched."""
    to_set = {c: bool(v) for c, v in feature_flags.items() if c in cols}
    if not to_set:
        return
    assignments = ", ".join(f"{c} = :p{i}" for i, c in enumerate(to_set))
    params: dict[str, Any] = {f"p{i}": v for i, (_, v) in enumerate(to_set.items())}
    params["wid"] = workspace_id
    await session.execute(
        text(f"UPDATE public.workspaces SET {assignments} WHERE id = :wid"),
        params,
    )


async def _workspace_exists(session: AsyncSession, workspace_id: str) -> bool:
    got = await session.execute(
        text("SELECT 1 FROM public.workspaces WHERE id = :wid"), {"wid": workspace_id}
    )
    return got.first() is not None


# ── plan catalog ────────────────────────────────────────────────────────────


def _plan_to_dict(row) -> dict:
    return {
        "id": str(row["id"]),
        "key": row["key"],
        "name": row["name"],
        "description": row["description"],
        "feature_flags": row["feature_flags"] or {},
        "limits": row["limits"] or {},
        "is_public": bool(row["is_public"]) if row["is_public"] is not None else True,
        "sort_order": int(row["sort_order"] or 0),
        "stripe_price_id": row["stripe_price_id"],
        "monthly_price_cents": row["monthly_price_cents"],
    }


async def list_plans(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(select(plans).order_by(plans.c.sort_order, plans.c.key))
    ).mappings().all()
    return [_plan_to_dict(r) for r in rows]


async def get_plan(session: AsyncSession, key: str) -> Optional[dict]:
    row = (
        await session.execute(select(plans).where(plans.c.key == key))
    ).mappings().first()
    return _plan_to_dict(row) if row else None


async def create_plan(
    session: AsyncSession, data: dict, actor, *, ip: Optional[str] = None
) -> dict:
    import uuid

    existing = await session.execute(select(plans.c.id).where(plans.c.key == data["key"]))
    if existing.first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="plan key already exists")

    now = _now()
    new_id = str(uuid.uuid4())
    await session.execute(
        insert(plans).values(
            id=new_id,
            key=data["key"],
            name=data.get("name"),
            description=data.get("description"),
            feature_flags=data.get("feature_flags") or {},
            limits=data.get("limits") or {},
            is_public=data.get("is_public", True),
            sort_order=data.get("sort_order", 0),
            monthly_price_cents=data.get("monthly_price_cents"),
            stripe_price_id=data.get("stripe_price_id"),  # Phase 6: price→plan key
            created_at=now,
            updated_at=now,
        )
    )
    await record_platform_event(
        session,
        actor_email=actor.email,
        action="plan.created",
        target_type="plan",
        target_id=new_id,
        state_after={"key": data["key"], "feature_flags": data.get("feature_flags") or {}},
        ip=ip,
    )
    return await get_plan(session, data["key"])


async def update_plan(
    session: AsyncSession, key: str, data: dict, actor, *, ip: Optional[str] = None
) -> dict:
    before = await get_plan(session, key)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="plan not found")

    editable = {
        k: data[k]
        for k in (
            "name",
            "description",
            "feature_flags",
            "limits",
            "is_public",
            "sort_order",
            "monthly_price_cents",
            "stripe_price_id",
        )
        if k in data and data[k] is not None
    }
    if not editable:
        return before

    editable["updated_at"] = _now()
    await session.execute(update(plans).where(plans.c.key == key).values(**editable))
    after = await get_plan(session, key)
    await record_platform_event(
        session,
        actor_email=actor.email,
        action="plan.updated",
        target_type="plan",
        target_id=before["id"],
        state_before=before,
        state_after=after,
        ip=ip,
    )
    return after


# ── apply / override ──────────────────────────────────────────────────────


async def _get_assignment(session: AsyncSession, workspace_id: str):
    return (
        await session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == workspace_id)
        )
    ).mappings().first()


async def apply_plan(
    session: AsyncSession,
    workspace_id: str,
    plan_key: str,
    actor,
    *,
    ip: Optional[str] = None,
) -> dict:
    """Upsert the workspace's plan + reconcile feature_* columns, audited.

    All writes happen in the caller's transaction; the endpoint commits once,
    so the assignment, the feature_* reconciliation, and the audit row are
    atomic together.
    """
    plan = await get_plan(session, plan_key)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="plan not found")
    if not await _workspace_exists(session, workspace_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace not found")

    cols = await _feature_columns(session)
    assignment = await _get_assignment(session, workspace_id)
    before = {
        "plan_key": assignment["plan_key"] if assignment else None,
        "feature_flags": await _read_features(session, workspace_id, cols),
    }

    now = _now()
    if assignment is None:
        await session.execute(
            insert(workspace_plans).values(
                workspace_id=workspace_id,
                plan_key=plan_key,
                plan_overrides={},
                updated_at=now,
            )
        )
    else:
        await session.execute(
            update(workspace_plans)
            .where(workspace_plans.c.workspace_id == workspace_id)
            .values(plan_key=plan_key, updated_at=now)
        )

    await _reconcile_features(session, workspace_id, plan["feature_flags"], cols)

    after = {
        "plan_key": plan_key,
        "feature_flags": await _read_features(session, workspace_id, cols),
    }
    await record_platform_event(
        session,
        actor_email=actor.email,
        action="plan.changed",
        target_type="workspace",
        target_id=workspace_id,
        target_workspace_id=workspace_id,
        state_before=before,
        state_after=after,
        ip=ip,
    )
    return after


async def set_overrides(
    session: AsyncSession,
    workspace_id: str,
    actor,
    *,
    flags: Optional[dict[str, bool]] = None,
    limits: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
) -> dict:
    """Grant/revoke one-off flag or limit overrides WITHOUT changing plan_key.

    Flag overrides are written straight to the feature_* column AND recorded in
    `workspace_plans.plan_overrides` so the deviation from the plan is visible.
    """
    if not await _workspace_exists(session, workspace_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace not found")

    cols = await _feature_columns(session)
    if flags:
        unknown = [k for k in flags if k not in cols]
        if unknown:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"unknown feature flag(s): {unknown}",
            )

    assignment = await _get_assignment(session, workspace_id)
    now = _now()
    if assignment is None:
        # No assignment yet (e.g. a workspace created after backfill) — default
        # to `free`, the same as the migration backfill, without applying it.
        await session.execute(
            insert(workspace_plans).values(
                workspace_id=workspace_id,
                plan_key="free",
                plan_overrides={},
                updated_at=now,
            )
        )
        assignment = await _get_assignment(session, workspace_id)

    before = {
        "plan_key": assignment["plan_key"],
        "plan_overrides": dict(assignment["plan_overrides"] or {}),
        "feature_flags": await _read_features(session, workspace_id, cols),
    }

    new_overrides = dict(assignment["plan_overrides"] or {})
    if flags:
        merged = dict(new_overrides.get("flags") or {})
        merged.update({k: bool(v) for k, v in flags.items()})
        new_overrides["flags"] = merged
    if limits:
        merged_l = dict(new_overrides.get("limits") or {})
        merged_l.update(limits)
        new_overrides["limits"] = merged_l

    await session.execute(
        update(workspace_plans)
        .where(workspace_plans.c.workspace_id == workspace_id)
        .values(plan_overrides=new_overrides, updated_at=now)  # plan_key unchanged
    )
    if flags:
        await _reconcile_features(session, workspace_id, flags, cols)

    after = {
        "plan_key": assignment["plan_key"],
        "plan_overrides": new_overrides,
        "feature_flags": await _read_features(session, workspace_id, cols),
    }
    await record_platform_event(
        session,
        actor_email=actor.email,
        action="plan.override",
        target_type="workspace",
        target_id=workspace_id,
        target_workspace_id=workspace_id,
        state_before=before,
        state_after=after,
        ip=ip,
    )
    return after
