"""
Phase 7 acceptance tests — alerts, digest, and settings.

  * a seeded signup spike (sharp drop) triggers EXACTLY one alert — one
    notification, one `alert.fired` audit row — and re-running the sweep while
    the condition persists fires nothing more (cooldown dedup);
  * an over-seat-limit workspace triggers a per-workspace alert; error-rate is
    skipped while Phase 5's table is absent;
  * the digest renders deterministically for a fixture (HTML + text);
  * the settings API is gated (no token / wrong capability → 403), persists a
    deep-merged partial update, and audits it (chain still verifies).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert, select

from app.services import alert_service, digest_service, notifier, settings_service
from tests.conftest import make_token

OPS_EMAIL = "ops@forgesop.test"
SUPPORT_EMAIL = "support@forgesop.test"
ADMIN_EMAIL = "admin@forgesop.test"

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _auth(email):
    return {"Authorization": f"Bearer {make_token(email=email)}"}


@pytest.fixture
def captured_emails(monkeypatch):
    """Capture notifier.send_email calls instead of delivering them."""
    sent: list[dict] = []

    async def _capture(to, subject, html, text=None):
        sent.append({"to": list(to), "subject": subject, "html": html, "text": text})

    monkeypatch.setattr(notifier, "send_email", _capture)
    return sent


@pytest.fixture
async def admin_operator(db_session, seed_admins):
    """Add a PLATFORM_ADMIN operator (seed_admins only has ops/support)."""
    from app.models.tables import platform_admins

    now = datetime.now(tz=timezone.utc)
    await db_session.execute(
        insert(platform_admins).values(
            id=str(uuid.uuid4()), email=ADMIN_EMAIL, role="PLATFORM_ADMIN",
            is_active=True, created_at=now, updated_at=now,
        )
    )
    await db_session.commit()


async def _seed_signup_drop(db_session):
    """Previous 7d window: 10 signups; recent 7d window: 1 signup → ~90% drop."""
    from app.models.public_tables import users, workspaces

    ws = str(uuid.uuid4())
    await db_session.execute(
        insert(workspaces).values(
            id=ws, name="DropCo", slug="dropco", is_suspended=False,
            created_at=NOW - timedelta(days=30),
        )
    )
    rows = []
    # 10 in the previous window (8–13 days ago).
    for i in range(10):
        rows.append({
            "id": str(uuid.uuid4()), "email": f"prev{i}@dropco.test",
            "first_name": "P", "last_name": str(i), "role": "MEMBER",
            "status": "ACTIVE", "workspace_id": ws, "login_count": 0,
            "created_at": NOW - timedelta(days=8 + (i % 5)),
        })
    # 1 in the recent window (2 days ago).
    rows.append({
        "id": str(uuid.uuid4()), "email": "recent@dropco.test",
        "first_name": "R", "last_name": "0", "role": "MEMBER",
        "status": "ACTIVE", "workspace_id": ws, "login_count": 0,
        "created_at": NOW - timedelta(days=2),
    })
    await db_session.execute(insert(users).values(rows))
    await db_session.commit()
    return ws


# ── signup-drop sweep: exactly one alert + cooldown dedup ────────────────────


async def test_signup_drop_detects_once(db_session, seed_admins):
    await _seed_signup_drop(db_session)
    thresholds = settings_service.SETTINGS_DEFAULTS["alert_thresholds"]
    alerts = await alert_service.detect_alerts(db_session, thresholds=thresholds, now=NOW)
    drops = [a for a in alerts if a["type"] == "signup_drop"]
    assert len(drops) == 1
    assert drops[0]["context"]["previous"] == 10
    assert drops[0]["context"]["recent"] == 1


async def test_seeded_spike_triggers_exactly_one_alert(
    db_session, seed_admins, captured_emails
):
    await _seed_signup_drop(db_session)
    # A recipient so the alert email path is exercised.
    from types import SimpleNamespace

    await settings_service.set_values(
        db_session, {"recipients": ["ops@forgesop.app"]},
        SimpleNamespace(email=OPS_EMAIL),
    )
    await db_session.commit()

    fired = await alert_service.run_sweeps(db_session, now=NOW)
    await db_session.commit()
    assert len(fired) == 1
    assert fired[0]["type"] == "signup_drop"
    assert len(captured_emails) == 1
    assert captured_emails[0]["to"] == ["ops@forgesop.app"]

    from app.models.tables import platform_audit
    from app.services.platform_audit import verify_chain

    audit_rows = (
        await db_session.execute(
            select(platform_audit).where(platform_audit.c.action == "alert.fired")
        )
    ).mappings().all()
    assert len(audit_rows) == 1
    assert audit_rows[0]["actor_email"] == alert_service.ALERT_ACTOR_EMAIL
    assert await verify_chain(db_session) is True

    # Re-running while the condition persists fires nothing (cooldown dedup).
    again = await alert_service.run_sweeps(db_session, now=NOW + timedelta(hours=1))
    await db_session.commit()
    assert again == []
    assert len(captured_emails) == 1  # no second email


async def test_no_alert_without_baseline(db_session, seed_admins):
    """No previous-window baseline ⇒ no drop alert (avoids small-number noise)."""
    thresholds = settings_service.SETTINGS_DEFAULTS["alert_thresholds"]
    alerts = await alert_service.detect_alerts(db_session, thresholds=thresholds, now=NOW)
    assert [a for a in alerts if a["type"] == "signup_drop"] == []


# ── over-seat-limit sweep ────────────────────────────────────────────────────


async def test_over_seat_limit_alert(db_session, seed_admins, seed_plans, seed_tenants):
    ws = seed_tenants["ws_a"]  # has 3 users seeded
    from app.models.tables import workspace_plans

    # Put it on a plan whose max_seats (2) is below its seat usage (3).
    await db_session.execute(
        insert(workspace_plans).values(
            workspace_id=ws, plan_key="free",
            plan_overrides={"limits": {"max_seats": 2}},
        )
    )
    await db_session.commit()

    alerts = await alert_service._detect_over_seat_limit(db_session)
    mine = [a for a in alerts if a["workspace_id"] == ws]
    assert len(mine) == 1
    assert mine[0]["context"]["seats_used"] == 3
    assert mine[0]["context"]["max_seats"] == 2


async def test_error_rate_skipped_without_phase5(db_session, seed_admins):
    thresholds = settings_service.SETTINGS_DEFAULTS["alert_thresholds"]
    alerts = await alert_service._detect_error_rate_spike(db_session, thresholds, NOW)
    assert alerts == []  # no api_request_metrics table yet


# ── digest renders for a fixture ─────────────────────────────────────────────


async def test_digest_renders_for_fixture(db_session, seed_admins, seed_tenants):
    digest = await digest_service.build_digest(db_session, period="weekly", now=NOW)
    assert digest["period"] == "weekly"
    assert digest["kpis"]["total_users"] == seed_tenants["total_users"]
    assert digest["kpis"]["total_workspaces"] == seed_tenants["total_workspaces"]

    html = digest_service.render_digest_html(digest)
    text = digest_service.render_digest_text(digest)
    assert "ForgeSOP operator digest" in html
    assert "Total users:" in text
    assert str(seed_tenants["total_users"]) in text


async def test_send_digest_emails_and_audits(
    db_session, seed_admins, seed_tenants, captured_emails
):
    from types import SimpleNamespace

    await settings_service.set_values(
        db_session, {"recipients": ["ops@forgesop.app"], "digest": {"enabled": True}},
        SimpleNamespace(email=OPS_EMAIL),
    )
    await db_session.commit()

    result = await digest_service.send_digest(db_session, period="daily", now=NOW)
    await db_session.commit()
    assert result is not None
    assert len(captured_emails) == 1
    assert "digest" in captured_emails[0]["subject"]

    from app.models.tables import platform_audit

    rows = (
        await db_session.execute(
            select(platform_audit).where(platform_audit.c.action == "digest.sent")
        )
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["metadata"]["period"] == "daily"


async def test_disabled_digest_not_sent(db_session, seed_admins, captured_emails):
    from types import SimpleNamespace

    await settings_service.set_values(
        db_session, {"digest": {"enabled": False}}, SimpleNamespace(email=OPS_EMAIL)
    )
    await db_session.commit()
    result = await digest_service.send_digest(db_session, now=NOW)
    assert result is None
    assert captured_emails == []


# ── settings API ─────────────────────────────────────────────────────────────


async def test_settings_requires_token(client, seed_admins):
    assert (await client.get("/v1/settings")).status_code == 403
    assert (await client.put("/v1/settings", json={"recipients": []})).status_code == 403


async def test_settings_forbidden_for_ops(client, seed_admins):
    """platform_settings.manage is PLATFORM_ADMIN only — OPS gets 403."""
    resp = await client.get("/v1/settings", headers=_auth(OPS_EMAIL))
    assert resp.status_code == 403


async def test_settings_get_returns_defaults(client, admin_operator):
    resp = await client.get("/v1/settings", headers=_auth(ADMIN_EMAIL))
    assert resp.status_code == 200
    body = resp.json()
    assert body["alert_thresholds"]["signup_drop_pct"] == 50
    assert body["digest"]["frequency"] == "weekly"
    assert body["recipients"] == []
    assert "_alert_state" not in body


async def test_settings_put_deep_merges_and_audits(client, admin_operator, db_session):
    resp = await client.put(
        "/v1/settings",
        headers=_auth(ADMIN_EMAIL),
        json={
            "alert_thresholds": {"signup_drop_pct": 30},
            "recipients": ["a@forgesop.app", "b@forgesop.app"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Updated field changed; sibling thresholds preserved (deep-merge).
    assert body["alert_thresholds"]["signup_drop_pct"] == 30
    assert body["alert_thresholds"]["signup_window_days"] == 7
    assert body["recipients"] == ["a@forgesop.app", "b@forgesop.app"]

    from app.models.tables import platform_audit
    from app.services.platform_audit import verify_chain

    rows = (
        await db_session.execute(
            select(platform_audit).where(platform_audit.c.action == "settings.updated")
        )
    ).mappings().all()
    assert len(rows) == 1
    assert await verify_chain(db_session) is True


async def test_settings_put_rejects_bad_frequency(client, admin_operator):
    resp = await client.put(
        "/v1/settings", headers=_auth(ADMIN_EMAIL), json={"digest": {"frequency": "hourly"}}
    )
    assert resp.status_code == 422
