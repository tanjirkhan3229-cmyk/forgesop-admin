"""
Phase 2 acceptance tests — plans.

  * applying `pro` flips exactly the feature_* columns pro.feature_flags lists
    (and leaves an unlisted column untouched), in one transaction, + writes a
    `plan.changed` audit row;
  * an override grants a single flag without changing plan_key;
  * stripe_* columns stay null;
  * the catalog endpoints + workspace PATCH are gated (403 without the
    capability / without a token).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from tests.conftest import make_token

OPS_EMAIL = "ops@forgesop.test"
SUPPORT_EMAIL = "support@forgesop.test"


def _auth(email=OPS_EMAIL):
    return {"Authorization": f"Bearer {make_token(email=email)}"}


async def _features(db_session, ws_id):
    row = (
        await db_session.execute(
            text(
                "SELECT feature_ehs_module, feature_risk_module, feature_phase_a "
                "FROM public.workspaces WHERE id = :id"
            ),
            {"id": ws_id},
        )
    ).mappings().first()
    return {k: bool(v) for k, v in row.items()}


# ── auth gating ─────────────────────────────────────────────────────────────


async def test_plans_endpoints_require_token(client, seed_admins, seed_plans):
    for method, path in [("get", "/v1/plans"), ("post", "/v1/plans")]:
        resp = await getattr(client, method)(path)
        assert resp.status_code == 403


async def test_plan_write_forbidden_for_support(client, seed_admins, seed_plans):
    """A PLATFORM_SUPPORT operator lacks plans.manage → 403."""
    resp = await client.get("/v1/plans", headers=_auth(SUPPORT_EMAIL))
    assert resp.status_code == 403


async def test_workspace_patch_forbidden_for_support(
    client, seed_admins, seed_plans, seed_tenants
):
    resp = await client.patch(
        f"/v1/workspaces/{seed_tenants['ws_a']}",
        headers=_auth(SUPPORT_EMAIL),
        json={"plan_key": "pro"},
    )
    assert resp.status_code == 403


# ── catalog ─────────────────────────────────────────────────────────────────


async def test_list_plans(client, seed_admins, seed_plans):
    resp = await client.get("/v1/plans", headers=_auth())
    assert resp.status_code == 200
    plans = resp.json()
    keys = {p["key"] for p in plans}
    assert keys == {"free", "pro", "enterprise"}
    pro = next(p for p in plans if p["key"] == "pro")
    assert pro["feature_flags"]["feature_ehs_module"] is True
    assert pro["stripe_price_id"] is None  # billing-later seam stays null


async def test_create_and_patch_plan(client, seed_admins, seed_plans):
    created = await client.post(
        "/v1/plans",
        headers=_auth(),
        json={
            "key": "team",
            "name": "Team",
            "feature_flags": {"feature_ehs_module": True},
            "limits": {"max_seats": 20},
        },
    )
    assert created.status_code == 201
    assert created.json()["key"] == "team"
    assert created.json()["stripe_price_id"] is None

    patched = await client.patch(
        "/v1/plans/team", headers=_auth(), json={"limits": {"max_seats": 30}}
    )
    assert patched.status_code == 200
    assert patched.json()["limits"]["max_seats"] == 30


# ── apply plan ──────────────────────────────────────────────────────────────


async def test_apply_pro_flips_exactly_listed_columns(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    before = await _features(db_session, ws)
    # Seeded state: ehs on, risk off, phase_a on.
    assert before == {
        "feature_ehs_module": True,
        "feature_risk_module": False,
        "feature_phase_a": True,
    }

    resp = await client.patch(
        f"/v1/workspaces/{ws}", headers=_auth(), json={"plan_key": "pro"}
    )
    assert resp.status_code == 200

    after = await _features(db_session, ws)
    # pro lists ehs + risk → both true; phase_a (NOT listed) is untouched.
    assert after == {
        "feature_ehs_module": True,
        "feature_risk_module": True,
        "feature_phase_a": True,
    }

    # workspace_plans row updated to pro.
    from app.models.tables import workspace_plans

    wp = (
        await db_session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == ws)
        )
    ).mappings().first()
    assert wp["plan_key"] == "pro"
    assert wp["stripe_customer_id"] is None
    assert wp["stripe_subscription_id"] is None


async def test_apply_plan_writes_audit_row(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await client.patch(f"/v1/workspaces/{ws}", headers=_auth(), json={"plan_key": "pro"})

    from app.models.tables import platform_audit
    from app.services.platform_audit import verify_chain

    rows = (
        await db_session.execute(
            select(platform_audit).where(platform_audit.c.action == "plan.changed")
        )
    ).mappings().all()
    assert len(rows) == 1
    event = rows[0]
    assert event["target_workspace_id"] == ws
    assert event["state_before"]["feature_flags"]["feature_risk_module"] is False
    assert event["state_after"]["feature_flags"]["feature_risk_module"] is True
    assert event["state_after"]["plan_key"] == "pro"
    # The hash chain still verifies.
    assert await verify_chain(db_session) is True


async def test_apply_unknown_plan_404(client, seed_admins, seed_plans, seed_tenants):
    resp = await client.patch(
        f"/v1/workspaces/{seed_tenants['ws_a']}",
        headers=_auth(),
        json={"plan_key": "does-not-exist"},
    )
    assert resp.status_code == 404


# ── overrides ───────────────────────────────────────────────────────────────


async def test_override_grants_single_flag_without_changing_plan(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    # Put the workspace on free first (free lists ehs+risk false).
    await client.patch(f"/v1/workspaces/{ws}", headers=_auth(), json={"plan_key": "free"})
    after_free = await _features(db_session, ws)
    assert after_free["feature_ehs_module"] is False
    assert after_free["feature_risk_module"] is False

    # Override: grant risk only — plan_key must stay free.
    resp = await client.patch(
        f"/v1/workspaces/{ws}",
        headers=_auth(),
        json={"flags": {"feature_risk_module": True}},
    )
    assert resp.status_code == 200

    feats = await _features(db_session, ws)
    assert feats["feature_risk_module"] is True   # the one granted flag
    assert feats["feature_ehs_module"] is False   # unchanged

    from app.models.tables import workspace_plans

    wp = (
        await db_session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == ws)
        )
    ).mappings().first()
    assert wp["plan_key"] == "free"  # NOT changed by the override
    assert wp["plan_overrides"]["flags"]["feature_risk_module"] is True
    assert wp["stripe_customer_id"] is None
    assert wp["stripe_subscription_id"] is None


async def test_override_unknown_flag_400(
    client, seed_admins, seed_plans, seed_tenants
):
    resp = await client.patch(
        f"/v1/workspaces/{seed_tenants['ws_a']}",
        headers=_auth(),
        json={"flags": {"feature_not_a_real_column": True}},
    )
    assert resp.status_code == 400


async def test_limit_override_recorded(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await client.patch(f"/v1/workspaces/{ws}", headers=_auth(), json={"plan_key": "free"})
    resp = await client.patch(
        f"/v1/workspaces/{ws}", headers=_auth(), json={"limits": {"max_seats": 99}}
    )
    assert resp.status_code == 200

    from app.models.tables import workspace_plans

    wp = (
        await db_session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == ws)
        )
    ).mappings().first()
    assert wp["plan_overrides"]["limits"]["max_seats"] == 99
    assert wp["plan_key"] == "free"
