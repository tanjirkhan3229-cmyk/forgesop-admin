"""
Acceptance tests for the Phase-0 security boundary.

Covers:
  * a tenant-style Supabase JWT is rejected at /v1/me with 403 (issuer/
    audience gate);
  * a platform-issuer token for an ACTIVE operator → 200 with role;
  * an inactive operator and a token for an unknown email → 403;
  * record_platform_event writes a verifying row, and verify_chain() holds
    over a 3-event chain.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    TENANT_AUDIENCE,
    TENANT_ISSUER,
    make_token,
)


# ── /v1/me gate ──────────────────────────────────────────────────────────


async def test_tenant_jwt_rejected(client, seed_admins):
    """A valid tenant Supabase JWT must not authenticate to the console."""
    token = make_token(
        email="ops@forgesop.test",
        issuer=TENANT_ISSUER,
        audience=TENANT_AUDIENCE,
    )
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_active_operator_accepted(client, seed_admins):
    token = make_token(email="ops@forgesop.test")
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "ops@forgesop.test"
    assert body["role"] == "PLATFORM_OPS"
    # OPS inherits SUPPORT and can apply plans / suspend, but cannot manage operators.
    assert "plan.apply" in body["capabilities"]
    assert "platform_admins.manage" not in body["capabilities"]


async def test_inactive_operator_rejected(client, seed_admins):
    token = make_token(email="former@forgesop.test")
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_unknown_operator_rejected(client, seed_admins):
    token = make_token(email="stranger@forgesop.test")
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_missing_token_rejected(client):
    resp = await client.get("/v1/me")
    assert resp.status_code == 403


async def test_expired_token_rejected(client, seed_admins):
    token = make_token(email="ops@forgesop.test", expired=True)
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


# ── hash chain ────────────────────────────────────────────────────────────


async def test_record_event_writes_verifying_row(db_session):
    from app.services.platform_audit import record_platform_event, verify_chain

    result = await record_platform_event(
        db_session,
        actor_email="ops@forgesop.test",
        action="workspace.suspended",
        target_type="workspace",
        target_id="11111111-1111-1111-1111-111111111111",
        state_before={"status": "ACTIVE"},
        state_after={"status": "SUSPENDED"},
        ip="203.0.113.7",
    )
    assert result["previous_hash"] == "GENESIS"
    assert len(result["hash"]) == 64
    assert await verify_chain(db_session) is True


async def test_three_event_chain_verifies(db_session):
    from app.services.platform_audit import record_platform_event, verify_chain

    r1 = await record_platform_event(
        db_session, actor_email="a@forgesop.test", action="plan.applied",
        target_workspace_id="22222222-2222-2222-2222-222222222222",
        state_after={"plan": "pro"},
    )
    r2 = await record_platform_event(
        db_session, actor_email="b@forgesop.test", action="user.deactivated",
        target_id="33333333-3333-3333-3333-333333333333",
    )
    r3 = await record_platform_event(
        db_session, actor_email="c@forgesop.test", action="platform_admin.created",
        target_id="44444444-4444-4444-4444-444444444444",
        state_after={"role": "PLATFORM_SUPPORT"},
    )

    # Properly linked: each event points at the prior tip.
    assert r1["previous_hash"] == "GENESIS"
    assert r2["previous_hash"] == r1["hash"]
    assert r3["previous_hash"] == r2["hash"]

    assert await verify_chain(db_session) is True


async def test_tamper_breaks_chain(db_session):
    """Mutating a stored row's fields must make verify_chain() fail."""
    from sqlalchemy import select, update

    from app.models.tables import platform_audit
    from app.services.platform_audit import record_platform_event, verify_chain

    await record_platform_event(
        db_session, actor_email="a@forgesop.test", action="plan.applied",
        state_after={"plan": "pro"},
    )
    await record_platform_event(
        db_session, actor_email="b@forgesop.test", action="workspace.suspended",
        state_after={"status": "SUSPENDED"},
    )
    assert await verify_chain(db_session) is True

    # Tamper with the first row's action without recomputing its hash.
    first = (
        await db_session.execute(
            select(platform_audit.c.audit_id).where(
                platform_audit.c.previous_hash == "GENESIS"
            )
        )
    ).scalar_one()
    await db_session.execute(
        update(platform_audit)
        .where(platform_audit.c.audit_id == first)
        .values(action="plan.applied.TAMPERED")
    )

    assert await verify_chain(db_session) is False
