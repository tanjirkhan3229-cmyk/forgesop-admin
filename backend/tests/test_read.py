"""
Phase 1 acceptance tests — the read-only cockpit.

  * every seeded workspace and user appears in the directory endpoints;
  * signup_series is exact for the seeded fixture timeline;
  * KPI counts are correct;
  * every read endpoint returns 403 without an operator token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import USER_OFFSETS_DAYS, make_token

OPERATOR_EMAIL = "ops@forgesop.test"

READ_ENDPOINTS = [
    "/v1/overview",
    "/v1/signups",
    "/v1/signups?range=7d",
    "/v1/workspaces",
    "/v1/users",
]


def _auth():
    return {"Authorization": f"Bearer {make_token(email=OPERATOR_EMAIL)}"}


# ── auth gate ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_read_endpoints_require_operator(client, seed_admins, path):
    """No operator token → 403 (never a data leak)."""
    resp = await client.get(path)
    assert resp.status_code == 403


async def test_workspace_detail_requires_operator(client, seed_admins, seed_tenants):
    resp = await client.get(f"/v1/workspaces/{seed_tenants['ws_a']}")
    assert resp.status_code == 403


# ── overview ──────────────────────────────────────────────────────────────


async def test_overview_kpis(client, seed_admins, seed_tenants):
    resp = await client.get("/v1/overview", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_users"] == 4
    assert body["total_workspaces"] == 2
    assert body["active_workspaces"] == 1  # Globex is suspended
    assert body["signups"]["last_24h"] == 1   # u1
    assert body["signups"]["last_7d"] == 2    # u1, u2
    assert body["signups"]["last_30d"] == 3   # u1, u2, u3 (u4 is 40d old)


# ── signups ─────────────────────────────────────────────────────────────────


async def test_signup_series_exact(client, seed_admins, seed_tenants):
    resp = await client.get("/v1/signups?range=30d", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "30d"
    assert len(body["series"]) == 30

    today = datetime.now(tz=timezone.utc).date()
    by_date = {p["date"]: p for p in body["series"]}

    # Users land on today (u1), today-3 (u2), today-15 (u3); u4 (40d) is excluded.
    assert by_date[today.isoformat()]["users"] == 1
    assert by_date[(today - timedelta(days=USER_OFFSETS_DAYS["u2"])).isoformat()]["users"] == 1
    assert by_date[(today - timedelta(days=USER_OFFSETS_DAYS["u3"])).isoformat()]["users"] == 1
    # Workspace Acme created 2 days ago; Globex (40d) is excluded.
    assert by_date[(today - timedelta(days=2)).isoformat()]["workspaces"] == 1

    assert body["totals"]["users"] == 3
    assert body["totals"]["workspaces"] == 1


async def test_signup_series_range_clamped(client, seed_admins, seed_tenants):
    # An unknown range falls back to the default (30d), never errors.
    resp = await client.get("/v1/signups?range=bogus", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["range"] == "30d"


# ── workspaces ────────────────────────────────────────────────────────────


async def test_list_workspaces_shows_all(client, seed_admins, seed_tenants):
    resp = await client.get("/v1/workspaces", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = {w["name"] for w in body["items"]}
    assert names == {"Acme Corp", "Globex"}

    acme = next(w for w in body["items"] if w["name"] == "Acme Corp")
    assert acme["member_count"] == 3
    assert acme["last_activity"] is not None
    assert acme["plan"] is None  # platform.workspace_plans does not exist in Phase 1


async def test_search_workspaces(client, seed_admins, seed_tenants):
    resp = await client.get("/v1/workspaces?search=glob", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Globex"


async def test_workspace_detail(client, seed_admins, seed_tenants):
    resp = await client.get(f"/v1/workspaces/{seed_tenants['ws_a']}", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Acme Corp"
    assert body["member_count"] == 3
    assert len(body["members"]) == 3
    assert {m["email"] for m in body["members"]} >= {"alice@acme.test", "bob@acme.test"}
    # Dynamic feature_* flags surfaced from SELECT *.
    assert body["feature_flags"]["feature_ehs_module"] is True
    assert body["feature_flags"]["feature_risk_module"] is False
    # Recent audit activity for this workspace (organization_id == ws id).
    assert len(body["recent_activity"]) == 2
    assert body["recent_activity"][0]["event_type"] == "document.published"


async def test_workspace_detail_404(client, seed_admins, seed_tenants):
    resp = await client.get(
        "/v1/workspaces/00000000-0000-0000-0000-000000000000", headers=_auth()
    )
    assert resp.status_code == 404


# ── users ─────────────────────────────────────────────────────────────────


async def test_list_users_shows_all(client, seed_admins, seed_tenants):
    resp = await client.get("/v1/users", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    emails = {u["email"] for u in body["items"]}
    assert emails == {
        "alice@acme.test", "bob@acme.test", "carol@acme.test", "dave@globex.test",
    }
    alice = next(u for u in body["items"] if u["email"] == "alice@acme.test")
    assert alice["name"] == "Alice Adams"
    assert alice["workspace_name"] == "Acme Corp"
    assert alice["role"] == "ADMIN"


async def test_filter_users_by_workspace(client, seed_admins, seed_tenants):
    resp = await client.get(
        f"/v1/users?workspace_id={seed_tenants['ws_a']}", headers=_auth()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert all(u["workspace_name"] == "Acme Corp" for u in body["items"])


async def test_filter_users_by_status_and_search(client, seed_admins, seed_tenants):
    resp = await client.get("/v1/users?status=PENDING", headers=_auth())
    assert resp.json()["total"] == 1

    resp = await client.get("/v1/users?search=globex", headers=_auth())
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "dave@globex.test"
