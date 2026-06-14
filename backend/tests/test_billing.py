"""
Phase 6 acceptance tests — Stripe billing.

  * a signature-verified webhook event flips the workspace's plan THROUGH the
    existing apply_plan (feature_* reconciled, plan_key updated) and populates
    workspace_plans.stripe_customer_id / stripe_subscription_id;
  * forged / replayed / unsigned payloads are rejected with 400, and no plan
    changes;
  * the audit chain records a Stripe-originated `billing.subscription.synced`
    event and still verifies;
  * cancellation downgrades to free; an unknown price is ignored (no change);
  * the manual operator override path is untouched (apply_plan is shared);
  * the read-only invoices view is gated, audited, and maps Stripe invoices.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import insert, select, text

from app.services import billing_service
from tests.conftest import make_token

OPS_EMAIL = "ops@forgesop.test"
SUPPORT_EMAIL = "support@forgesop.test"

WEBHOOK_SECRET = "whsec_test_secret_123"
PRO_PRICE = "price_pro_monthly"


def _auth(email=OPS_EMAIL):
    return {"Authorization": f"Bearer {make_token(email=email)}"}


def _sign(payload: bytes, *, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    """Build a valid `Stripe-Signature` header for `payload` (Stripe's scheme)."""
    ts = ts if ts is not None else int(time.time())
    sig = hmac.new(secret.encode("utf-8"), b"%d." % ts + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _subscription_event(
    *, event_type: str, workspace_id: str, price_id: str | None = PRO_PRICE,
    customer="cus_123", subscription="sub_123", event_id="evt_1",
) -> bytes:
    obj: dict = {
        "id": subscription,
        "customer": customer,
        "metadata": {"workspace_id": workspace_id},
    }
    if price_id is not None:
        obj["items"] = {"data": [{"price": {"id": price_id}}]}
    return json.dumps(
        {"id": event_id, "type": event_type, "data": {"object": obj}}
    ).encode("utf-8")


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


@pytest.fixture(autouse=True)
def _stripe_secret(monkeypatch):
    """Enable the webhook by configuring the signing secret for the suite."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(app_settings, "STRIPE_WEBHOOK_TOLERANCE_SECONDS", 300)
    yield


async def _price_a_plan(client, price_id=PRO_PRICE, key="pro"):
    """Map a Stripe price onto a catalog plan via the (gated) plans API."""
    resp = await client.patch(
        f"/v1/plans/{key}", headers=_auth(), json={"stripe_price_id": price_id}
    )
    assert resp.status_code == 200
    assert resp.json()["stripe_price_id"] == price_id


# ── plan catalog: stripe_price_id is now settable (the mapping key) ──────────


async def test_plan_stripe_price_id_settable(client, seed_admins, seed_plans):
    await _price_a_plan(client)
    plans = (await client.get("/v1/plans", headers=_auth())).json()
    pro = next(p for p in plans if p["key"] == "pro")
    assert pro["stripe_price_id"] == PRO_PRICE


# ── webhook flips the plan via apply_plan ────────────────────────────────────


async def test_webhook_flips_plan_via_apply_plan(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)

    # Seeded: ehs on, risk off, phase_a on.
    before = await _features(db_session, ws)
    assert before["feature_risk_module"] is False

    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws
    )
    resp = await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": _sign(payload)},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert resp.json()["plan_key"] == "pro"

    # apply_plan reconciled exactly the columns pro lists; phase_a untouched.
    after = await _features(db_session, ws)
    assert after == {
        "feature_ehs_module": True,
        "feature_risk_module": True,
        "feature_phase_a": True,
    }

    # workspace_plans now on pro WITH the Stripe linkage populated.
    from app.models.tables import workspace_plans

    wp = (
        await db_session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == ws)
        )
    ).mappings().first()
    assert wp["plan_key"] == "pro"
    assert wp["stripe_customer_id"] == "cus_123"
    assert wp["stripe_subscription_id"] == "sub_123"


async def test_webhook_writes_audit_and_chain_verifies(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)
    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws, event_id="evt_audit"
    )
    await client.post(
        "/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )

    from app.models.tables import platform_audit
    from app.services.platform_audit import verify_chain

    rows = (
        await db_session.execute(
            select(platform_audit).where(
                platform_audit.c.action == "billing.subscription.synced"
            )
        )
    ).mappings().all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev["actor_email"] == billing_service.WEBHOOK_ACTOR_EMAIL
    assert ev["target_workspace_id"] == ws
    assert ev["state_after"]["plan_key"] == "pro"
    assert ev["metadata"]["stripe_event_id"] == "evt_audit"
    assert ev["metadata"]["stripe_price_id"] == PRO_PRICE
    # apply_plan's own plan.changed row is present too, and the chain verifies.
    assert await verify_chain(db_session) is True


# ── signature rejection ──────────────────────────────────────────────────────


async def test_forged_signature_rejected(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)
    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws
    )

    # Signed with the WRONG secret → 400, and the plan must NOT change.
    bad = _sign(payload, secret="whsec_attacker")
    resp = await client.post(
        "/v1/billing/webhook", content=payload, headers={"Stripe-Signature": bad}
    )
    assert resp.status_code == 400

    from app.models.tables import workspace_plans

    wp = (
        await db_session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == ws)
        )
    ).first()
    assert wp is None  # no assignment created → apply_plan never ran


async def test_tampered_payload_rejected(client, seed_admins, seed_plans, seed_tenants):
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)
    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws
    )
    sig = _sign(payload)  # signature for the ORIGINAL payload
    tampered = payload.replace(b"sub_123", b"sub_evil")
    resp = await client.post(
        "/v1/billing/webhook", content=tampered, headers={"Stripe-Signature": sig}
    )
    assert resp.status_code == 400


async def test_missing_signature_rejected(client, seed_admins, seed_plans, seed_tenants):
    ws = seed_tenants["ws_a"]
    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws
    )
    resp = await client.post("/v1/billing/webhook", content=payload)
    assert resp.status_code == 400


async def test_stale_timestamp_rejected(client, seed_admins, seed_plans, seed_tenants):
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)
    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws
    )
    old = _sign(payload, ts=int(time.time()) - 10_000)  # well past 300s tolerance
    resp = await client.post(
        "/v1/billing/webhook", content=payload, headers={"Stripe-Signature": old}
    )
    assert resp.status_code == 400


# ── mapping edge cases ───────────────────────────────────────────────────────


async def test_unknown_price_is_ignored(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    # No plan is mapped to this price → ignored, no change.
    payload = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws, price_id="price_unmapped"
    )
    resp = await client.post(
        "/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert resp.json()["reason"] == "unknown_price"

    from app.models.tables import workspace_plans

    wp = (
        await db_session.execute(
            select(workspace_plans).where(workspace_plans.c.workspace_id == ws)
        )
    ).first()
    assert wp is None


async def test_cancellation_downgrades_to_free(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)
    # Subscribe to pro first.
    created = _subscription_event(
        event_type="customer.subscription.created", workspace_id=ws
    )
    await client.post(
        "/v1/billing/webhook", content=created, headers={"Stripe-Signature": _sign(created)}
    )
    assert (await _features(db_session, ws))["feature_risk_module"] is True

    # Cancel → downgraded to free (free lists ehs+risk = false).
    deleted = _subscription_event(
        event_type="customer.subscription.deleted", workspace_id=ws, price_id=None
    )
    resp = await client.post(
        "/v1/billing/webhook", content=deleted, headers={"Stripe-Signature": _sign(deleted)}
    )
    assert resp.status_code == 200
    assert resp.json()["plan_key"] == "free"

    after = await _features(db_session, ws)
    assert after["feature_ehs_module"] is False
    assert after["feature_risk_module"] is False


async def test_resolves_workspace_by_existing_customer(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    """When the event omits metadata.workspace_id, fall back to the linked customer."""
    ws = seed_tenants["ws_a"]
    await _price_a_plan(client)
    from app.models.tables import workspace_plans

    # Pre-link the customer to this workspace.
    await db_session.execute(
        insert(workspace_plans).values(
            workspace_id=ws, plan_key="free", plan_overrides={},
            stripe_customer_id="cus_linked",
        )
    )
    await db_session.commit()

    payload = json.dumps(
        {
            "id": "evt_nometa",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_x",
                    "customer": "cus_linked",
                    "metadata": {},  # no workspace_id
                    "items": {"data": [{"price": {"id": PRO_PRICE}}]},
                }
            },
        }
    ).encode("utf-8")
    resp = await client.post(
        "/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert resp.json()["workspace_id"] == ws


# ── manual override path retained (shares apply_plan) ────────────────────────


async def test_manual_plan_change_still_works(
    client, seed_admins, seed_plans, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    resp = await client.patch(
        f"/v1/workspaces/{ws}", headers=_auth(), json={"plan_key": "pro"}
    )
    assert resp.status_code == 200
    assert (await _features(db_session, ws))["feature_risk_module"] is True


# ── read-only invoices view ──────────────────────────────────────────────────


_FAKE_INVOICES = [
    {
        "id": "in_1", "number": "F-0001", "status": "paid",
        "amount_due": 4900, "amount_paid": 4900, "currency": "usd",
        "created": 1_700_000_000, "hosted_invoice_url": "https://pay.stripe/in_1",
        "invoice_pdf": "https://pay.stripe/in_1.pdf",
    }
]


async def test_invoices_requires_token(client, seed_admins, seed_tenants):
    resp = await client.get(f"/v1/billing/invoices?workspace_id={seed_tenants['ws_a']}")
    assert resp.status_code == 403


async def test_invoices_returns_mapped_invoices(
    client, seed_admins, seed_plans, seed_tenants, db_session, monkeypatch
):
    ws = seed_tenants["ws_a"]
    from app.models.tables import workspace_plans

    await db_session.execute(
        insert(workspace_plans).values(
            workspace_id=ws, plan_key="pro", plan_overrides={},
            stripe_customer_id="cus_inv",
        )
    )
    await db_session.commit()

    async def _fake_fetcher(customer_id, limit):
        assert customer_id == "cus_inv"
        return _FAKE_INVOICES

    monkeypatch.setattr(billing_service, "_invoice_fetcher", _fake_fetcher)

    # A read-only SUPPORT operator (tenant.read) can view invoices.
    resp = await client.get(
        f"/v1/billing/invoices?workspace_id={ws}", headers=_auth(SUPPORT_EMAIL)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_id"] == "cus_inv"
    assert body["invoices"][0]["number"] == "F-0001"
    assert body["invoices"][0]["amount_due"] == 4900


async def test_invoices_empty_without_customer(
    client, seed_admins, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]  # no workspace_plans row → no customer
    resp = await client.get(
        f"/v1/billing/invoices?workspace_id={ws}", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json() == {"customer_id": None, "invoices": []}


async def test_invoices_read_is_audited(
    client, seed_admins, seed_tenants, db_session
):
    ws = seed_tenants["ws_a"]
    await client.get(f"/v1/billing/invoices?workspace_id={ws}", headers=_auth())

    from app.models.tables import platform_audit
    from app.services.platform_audit import verify_chain

    rows = (
        await db_session.execute(
            select(platform_audit).where(
                platform_audit.c.action == "billing.invoices.viewed"
            )
        )
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["target_workspace_id"] == ws
    assert await verify_chain(db_session) is True
