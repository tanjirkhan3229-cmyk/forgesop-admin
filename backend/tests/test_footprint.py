"""
Phase 3 acceptance tests — customer footprints.

Covers the four acceptance criteria from the build plan:
  1. the rollup writes exactly one row per workspace per day (and is idempotent);
  2. engagement_score is deterministic for fixed inputs;
  3. the "over seat limit" filter returns only workspaces whose seats_used
     exceeds the plan seat limit;
  4. the "inactive >= N days" filter respects the threshold.

Plus: the trend detail endpoint, and the operator auth gate on both routes.

Seeding is local (not the shared conftest tenants) so seats, plan limits, and
activity windows are pinned to exact values the asserts depend on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

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
from app.models.tables import customer_footprint_daily, signup_events, workspace_plans
from app.services import footprint_service
from tests.conftest import make_token

OPERATOR_EMAIL = "ops@forgesop.test"


def _auth():
    return {"Authorization": f"Bearer {make_token(email=OPERATOR_EMAIL)}"}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _add_workspace(session, *, name, plan_key, active_seats, last_active_delta, modules=0):
    """Seed one workspace: `active_seats` ACTIVE users (each last-active
    `last_active_delta` ago) + one inactive user, a plan assignment, and
    `modules` objects across each adoption table."""
    now = _now()
    ws_id = str(uuid.uuid4())
    await session.execute(
        workspaces.insert(),
        [{"id": ws_id, "name": name, "slug": name.lower(), "is_suspended": False,
          "created_at": now - timedelta(days=60)}],
    )

    user_rows = []
    for i in range(active_seats):
        user_rows.append({
            "id": str(uuid.uuid4()), "email": f"{name.lower()}-u{i}@t.test",
            "first_name": "U", "last_name": str(i), "role": "MEMBER",
            "status": "ACTIVE", "workspace_id": ws_id, "login_count": 1,
            "last_active_at": now - last_active_delta,
            "created_at": now - timedelta(days=50),
        })
    # one DEACTIVATED user — must NOT count toward seats_used.
    user_rows.append({
        "id": str(uuid.uuid4()), "email": f"{name.lower()}-gone@t.test",
        "first_name": "Gone", "last_name": "User", "role": "MEMBER",
        "status": "DEACTIVATED", "workspace_id": ws_id, "login_count": 0,
        "last_active_at": None, "created_at": now - timedelta(days=50),
    })
    await session.execute(users.insert(), user_rows)

    await session.execute(
        workspace_plans.insert(),
        [{"workspace_id": ws_id, "plan_key": plan_key, "plan_overrides": {},
          "updated_at": now}],
    )

    for table in (sops, ehs_incidents, capas, risks):
        if modules:
            await session.execute(
                table.insert(),
                [{"id": str(uuid.uuid4()), "workspace_id": ws_id, "status": "OPEN",
                  "created_at": now - timedelta(days=5)} for _ in range(modules)],
            )

    # an audit event today so active_users_* is non-zero for active workspaces.
    if active_seats and last_active_delta < timedelta(days=1):
        await session.execute(
            audit_trail.insert(),
            [{"audit_id": str(uuid.uuid4()), "timestamp": now - timedelta(hours=2),
              "event_type": "doc.published", "action": "publish",
              "actor_id": user_rows[0]["id"], "actor_email": user_rows[0]["email"],
              "actor_name": "U 0", "organization_id": ws_id}],
        )
    return ws_id


@pytest.fixture
async def world(db_session, seed_plans):
    """Two workspaces on the `free` plan (max_seats=5):
      * over  — 6 ACTIVE seats, active in the last hour  → over limit, recent
      * ok    — 2 ACTIVE seats, last active 40 days ago  → under limit, inactive
    """
    over = await _add_workspace(
        db_session, name="Over", plan_key="free", active_seats=6,
        last_active_delta=timedelta(hours=1), modules=2,
    )
    ok = await _add_workspace(
        db_session, name="Okay", plan_key="free", active_seats=2,
        last_active_delta=timedelta(days=40), modules=1,
    )
    await db_session.commit()
    return {"over": over, "ok": ok, "free_limit": seed_plans["free"]["limits"]["max_seats"]}


# ── 2. engagement_score is deterministic for fixed inputs ───────────────────


def test_engagement_score_is_deterministic():
    day = datetime(2026, 6, 14, tzinfo=timezone.utc).date()

    # Fully engaged: active today (recency 1.0), all 4 modules (breadth 1.0),
    # 5/10 seats (util 0.5) → 100*(0.5*1 + 0.3*1 + 0.2*0.5) = 90.00
    full = footprint_service.compute_engagement_score(
        day=day,
        last_active_at=datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc),
        module_counts={"sops_count": 3, "incidents_count": 1, "capas_count": 2, "risks_count": 4},
        seats_used=5, seat_limit=10,
    )
    assert full == 90.0

    # Half-recency (15d), one module (breadth 0.25), no seat signal →
    # 100*(0.5*0.5 + 0.3*0.25 + 0) = 32.50
    partial = footprint_service.compute_engagement_score(
        day=day,
        last_active_at=datetime(2026, 5, 30, tzinfo=timezone.utc),
        module_counts={"sops_count": 1, "incidents_count": 0, "capas_count": 0, "risks_count": 0},
        seats_used=0, seat_limit=10,
    )
    assert partial == 32.5

    # No activity, no modules, no limit → 0.00
    cold = footprint_service.compute_engagement_score(
        day=day, last_active_at=None,
        module_counts={"sops_count": 0, "incidents_count": 0, "capas_count": 0, "risks_count": 0},
        seats_used=3, seat_limit=None,
    )
    assert cold == 0.0

    # Same inputs twice → identical (purity).
    assert full == footprint_service.compute_engagement_score(
        day=day,
        last_active_at=datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc),
        module_counts={"sops_count": 3, "incidents_count": 1, "capas_count": 2, "risks_count": 4},
        seats_used=5, seat_limit=10,
    )


# ── 1. rollup writes one row per workspace per day (idempotent) ─────────────


async def test_rollup_one_row_per_workspace_per_day(db_session, world):
    day = _now().date()

    written = await footprint_service.run_footprint_rollup(db_session, day)
    await db_session.commit()
    assert written == 2  # one per seeded workspace

    rows = (await db_session.execute(customer_footprint_daily.select())).mappings().all()
    assert len(rows) == 2
    assert {str(r["workspace_id"]) for r in rows} == {world["over"], world["ok"]}
    assert all(str(r["day"]) == day.isoformat() for r in rows)

    # ACTIVE seats only (the DEACTIVATED user is excluded).
    by_ws = {str(r["workspace_id"]): r for r in rows}
    assert by_ws[world["over"]]["seats_used"] == 6
    assert by_ws[world["ok"]]["seats_used"] == 2

    # Idempotent: re-running the same day does not duplicate.
    again = await footprint_service.run_footprint_rollup(db_session, day)
    await db_session.commit()
    assert again == 2
    rows2 = (await db_session.execute(customer_footprint_daily.select())).all()
    assert len(rows2) == 2


async def test_rollup_engagement_is_deterministic_end_to_end(db_session, world):
    """Two rollups of the same fixed day yield the identical engagement score."""
    day = datetime(2026, 6, 14, tzinfo=timezone.utc).date()
    fp_a = await footprint_service.compute_footprint(db_session, world["over"], day)
    fp_b = await footprint_service.compute_footprint(db_session, world["over"], day)
    assert fp_a["engagement_score"] == fp_b["engagement_score"]
    assert fp_a == fp_b


# ── 3. over-seat-limit filter ───────────────────────────────────────────────


async def test_over_seat_limit_filter(db_session, world):
    day = _now().date()
    await footprint_service.run_footprint_rollup(db_session, day)
    await db_session.commit()

    result = await footprint_service.list_footprints(db_session, over_seat_limit=True)
    ids = [i["workspace_id"] for i in result["items"]]
    assert ids == [world["over"]]  # only the 6-seat workspace exceeds free's 5
    only = result["items"][0]
    assert only["seats_used"] == 6
    assert only["seat_limit"] == world["free_limit"] == 5
    assert only["over_seat_limit"] is True

    # Without the filter, both workspaces are listed.
    everything = await footprint_service.list_footprints(db_session)
    assert {i["workspace_id"] for i in everything["items"]} == {world["over"], world["ok"]}


# ── 4. inactive >= N days filter ────────────────────────────────────────────


async def test_inactive_days_filter(db_session, world):
    day = _now().date()
    await footprint_service.run_footprint_rollup(db_session, day)
    await db_session.commit()

    # ok was last active 40d ago; over within the last hour.
    inactive_30 = await footprint_service.list_footprints(db_session, inactive_days=30)
    assert [i["workspace_id"] for i in inactive_30["items"]] == [world["ok"]]

    # A threshold above 40d catches neither.
    inactive_60 = await footprint_service.list_footprints(db_session, inactive_days=60)
    assert inactive_60["items"] == []

    # A small threshold catches both (over: ~0d is not >= 1, so only ok again).
    inactive_1 = await footprint_service.list_footprints(db_session, inactive_days=1)
    assert [i["workspace_id"] for i in inactive_1["items"]] == [world["ok"]]


# ── detail + trend ──────────────────────────────────────────────────────────


async def test_footprint_detail_has_trend(db_session, world):
    # Two days of snapshots so the trend has > 1 point.
    today = _now().date()
    yesterday = today - timedelta(days=1)
    await footprint_service.run_footprint_rollup(db_session, yesterday)
    await footprint_service.run_footprint_rollup(db_session, today)
    await db_session.commit()

    detail = await footprint_service.get_footprint_detail(db_session, world["over"])
    assert detail is not None
    assert detail["workspace_id"] == world["over"]
    assert detail["seat_limit"] == 5
    assert len(detail["trend"]) == 2
    # oldest → newest
    assert detail["trend"][0]["day"] == yesterday.isoformat()
    assert detail["trend"][1]["day"] == today.isoformat()
    assert detail["latest"]["seats_used"] == 6
    assert detail["latest"]["over_seat_limit"] is True

    assert await footprint_service.get_footprint_detail(db_session, str(uuid.uuid4())) is None


# ── signup funnel rollup ────────────────────────────────────────────────────


async def test_signup_funnel_rollup_is_idempotent(db_session, world):
    inserted = await footprint_service.run_signup_funnel_rollup(db_session)
    await db_session.commit()
    # 3 users in Over (6 active + 1 deactivated = 7) + Okay (2 + 1 = 3) = 10.
    assert inserted == 10
    rows = (await db_session.execute(signup_events.select())).mappings().all()
    assert len(rows) == 10
    assert all(r["source"] == "backfill" for r in rows)
    # free-plan workspaces → plan_at_signup recorded.
    assert {r["plan_at_signup"] for r in rows} == {"free"}

    # Re-running inserts nothing new (idempotent by user_id).
    again = await footprint_service.run_signup_funnel_rollup(db_session)
    await db_session.commit()
    assert again == 0
    assert len((await db_session.execute(signup_events.select())).all()) == 10


# ── API routes + auth gate ──────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/v1/footprints", "/v1/footprints/anything"])
async def test_footprint_endpoints_require_operator(client, seed_admins, path):
    assert (await client.get(path)).status_code == 403


async def test_footprints_directory_endpoint(client, seed_admins, db_session, world):
    day = _now().date()
    await footprint_service.run_footprint_rollup(db_session, day)
    await db_session.commit()

    resp = await client.get("/v1/footprints?over_seat_limit=true", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["workspace_id"] == world["over"]

    # sortable: by seats_used ascending → Okay (2) before Over (6).
    resp2 = await client.get("/v1/footprints?sort=seats_used&order=asc", headers=_auth())
    ids = [i["workspace_id"] for i in resp2.json()["items"]]
    assert ids == [world["ok"], world["over"]]


async def test_footprint_detail_endpoint(client, seed_admins, db_session, world):
    await footprint_service.run_footprint_rollup(db_session, _now().date())
    await db_session.commit()

    resp = await client.get(f"/v1/footprints/{world['over']}", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Over"
    assert body["latest"]["over_seat_limit"] is True
    assert len(body["trend"]) == 1

    missing = await client.get(f"/v1/footprints/{uuid.uuid4()}", headers=_auth())
    assert missing.status_code == 404
