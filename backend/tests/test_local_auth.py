"""
Local email+password auth (PLATFORM_LOCAL_AUTH mode).

Proves the full flow the operator described:
  * first login (no password yet) → `password_set_required`;
  * set-password works once, is rejected the second time, and enforces length;
  * sign in with email+password → a session token that authenticates /v1/me;
  * wrong password / unknown email → 401; no token, no /v1/me access;
  * the auth routes are absent (404) when local auth is off;
  * login + password-set are audited and the chain still verifies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import insert, select

EMAIL = "tanjir.khan3229@gmail.com"
GOOD_PW = "hOsfLVf&2jOdg6tQtwIK=p"
SESSION_SECRET = "test-session-secret-please-rotate"


@pytest.fixture(autouse=True)
def _enable_local_auth(monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "PLATFORM_LOCAL_AUTH", True)
    monkeypatch.setattr(app_settings, "PLATFORM_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setattr(app_settings, "PLATFORM_SESSION_TTL_HOURS", 12)
    monkeypatch.setattr(app_settings, "PLATFORM_SETUP_TOKEN", None)
    yield


@pytest_asyncio.fixture
async def admin_no_password(db_session):
    """An active operator with no password set yet (first-login state)."""
    from app.models.tables import platform_admins

    now = datetime.now(tz=timezone.utc)
    await db_session.execute(
        insert(platform_admins).values(
            id=str(uuid.uuid4()), email=EMAIL, role="PLATFORM_ADMIN",
            is_active=True, created_at=now, updated_at=now,
        )
    )
    await db_session.commit()


# ── first-login → set-password → sign-in ─────────────────────────────────────


async def test_first_login_requires_password_set(client, admin_no_password):
    resp = await client.post("/v1/auth/login", json={"email": EMAIL, "password": "anything"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "password_set_required"


async def test_set_password_then_login_and_access_me(client, admin_no_password, db_session):
    # Set the password (first time).
    setp = await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": GOOD_PW})
    assert setp.status_code == 200
    assert setp.json()["status"] == "ok"

    # Setting it a second time is rejected.
    again = await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": GOOD_PW})
    assert again.status_code == 409

    # Sign in with email + password → a session token.
    login = await client.post("/v1/auth/login", json={"email": EMAIL, "password": GOOD_PW})
    assert login.status_code == 200
    body = login.json()
    assert body["status"] == "ok"
    token = body["token"]
    assert body["token_type"] == "bearer"

    # The session token authenticates the gated /v1/me.
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL
    assert me.json()["role"] == "PLATFORM_ADMIN"

    # password_set_at was stamped.
    from app.models.tables import platform_admins

    row = (
        await db_session.execute(
            select(platform_admins.c.password_hash, platform_admins.c.password_set_at).where(
                platform_admins.c.email == EMAIL
            )
        )
    ).mappings().first()
    assert row["password_hash"] and row["password_hash"].startswith("pbkdf2_sha256$")
    assert row["password_set_at"] is not None


async def test_wrong_password_rejected(client, admin_no_password):
    await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": GOOD_PW})
    resp = await client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong-password-xx"})
    assert resp.status_code == 401


async def test_unknown_email_rejected(client, admin_no_password):
    resp = await client.post(
        "/v1/auth/login", json={"email": "stranger@example.com", "password": GOOD_PW}
    )
    assert resp.status_code == 401


async def test_set_password_too_short(client, admin_no_password):
    resp = await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": "short"})
    assert resp.status_code == 422  # pydantic min_length


async def test_no_token_still_403_on_gated_route(client, admin_no_password):
    assert (await client.get("/v1/me")).status_code == 403


async def test_tampered_token_rejected(client, admin_no_password):
    await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": GOOD_PW})
    token = (
        await client.post("/v1/auth/login", json={"email": EMAIL, "password": GOOD_PW})
    ).json()["token"]
    bad = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 403


# ── audit ────────────────────────────────────────────────────────────────────


async def test_login_and_set_password_are_audited(client, admin_no_password, db_session):
    await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": GOOD_PW})
    await client.post("/v1/auth/login", json={"email": EMAIL, "password": GOOD_PW})

    from app.models.tables import platform_audit
    from app.services.platform_audit import verify_chain

    actions = [
        r[0]
        for r in (
            await db_session.execute(select(platform_audit.c.action))
        ).all()
    ]
    assert "auth.password_set" in actions
    assert "auth.login" in actions
    assert await verify_chain(db_session) is True


# ── routes disabled when local auth is off ───────────────────────────────────


async def test_auth_routes_absent_without_local_auth(client, admin_no_password, monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "PLATFORM_LOCAL_AUTH", False)
    resp = await client.post("/v1/auth/login", json={"email": EMAIL, "password": GOOD_PW})
    assert resp.status_code == 404


# ── unit: setup-token gate ───────────────────────────────────────────────────


async def test_setup_token_required_when_configured(client, admin_no_password, monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "PLATFORM_SETUP_TOKEN", "secret-bootstrap")
    # Wrong/absent token → 403.
    bad = await client.post("/v1/auth/set-password", json={"email": EMAIL, "password": GOOD_PW})
    assert bad.status_code == 403
    # Correct token → ok.
    ok = await client.post(
        "/v1/auth/set-password",
        json={"email": EMAIL, "password": GOOD_PW, "setup_token": "secret-bootstrap"},
    )
    assert ok.status_code == 200
