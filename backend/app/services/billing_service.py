"""
Stripe billing bridge (Phase 6 — optional).

This is the ONLY Stripe touch-point in the service. It does two things and
deliberately nothing more:

  1. **Webhook → plan.** A signature-verified Stripe webhook maps a subscribed
     `stripe_price_id → platform.plans.key` and calls the EXISTING
     `plan_service.apply_plan(...)`. Reconciliation of `public.workspaces.feature_*`
     is therefore unchanged — billing is just another *caller* of `apply_plan`,
     exactly like an operator's manual `PATCH /v1/workspaces/{id}`. The manual
     override path is fully retained; Stripe and operators share one code path.

  2. **Read-only invoices.** `list_invoices` fetches a workspace's Stripe
     invoices (by `workspace_plans.stripe_customer_id`) for display. It never
     writes to Stripe and never writes a tenant table.

Signature verification is implemented here with the standard library (HMAC-
SHA256 over `"{t}.{payload}"`, the documented Stripe scheme) so it is exact,
offline, and unit-testable without the Stripe SDK or a network round-trip — the
same explicit-verification philosophy as `platform_auth.verify_platform_token`.
The invoices fetch lazily imports the `stripe` SDK and degrades to an empty list
when billing is unconfigured, so importing this module never requires the SDK.

Every sync + every invoice read is audited via `platform_audit`. The webhook
acts as a synthetic system operator (`WEBHOOK_ACTOR_EMAIL`) so the audit chain
records that the change originated from Stripe, not a human operator.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tables import plans, workspace_plans
from app.services import plan_service
from app.services.platform_audit import record_platform_event

logger = logging.getLogger(__name__)

# The audit actor for Stripe-originated changes. Not a real operator row — it
# documents in the hash chain that a plan change came from the billing webhook.
WEBHOOK_ACTOR_EMAIL = "stripe-webhook@forgesop.platform"

# Subscription lifecycle events that move a workspace between plans. created /
# updated map the active price → plan; deleted (cancellation) downgrades to free.
_SUBSCRIPTION_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)

# Plan a cancelled subscription falls back to. Must exist in the catalog (the
# 0002 migration seeds it and backfills every workspace onto it).
_CANCELLED_PLAN_KEY = "free"


@dataclass(frozen=True)
class _SystemActor:
    """Minimal actor shim for `apply_plan` (which only reads `.email`)."""

    email: str


_SYSTEM_ACTOR = _SystemActor(email=WEBHOOK_ACTOR_EMAIL)


# ── signature verification (stdlib; no SDK / no network) ─────────────────────


class StripeSignatureError(Exception):
    """Raised when a webhook payload fails signature verification."""


def _parse_signature_header(header: str) -> tuple[Optional[int], list[str]]:
    """Parse Stripe's `t=...,v1=...,v1=...` header into (timestamp, [v1...])."""
    timestamp: Optional[int] = None
    signatures: list[str] = []
    for part in header.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                timestamp = None
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def verify_signature(
    payload: bytes,
    sig_header: str,
    *,
    secret: Optional[str] = None,
    tolerance: Optional[int] = None,
    now_ts: Optional[int] = None,
) -> None:
    """Verify a Stripe webhook signature; raise `StripeSignatureError` on failure.

    Recomputes HMAC-SHA256(secret, "{t}.{payload}") and constant-time-compares it
    against every `v1` signature in the header, then enforces the replay
    tolerance. Mirrors `stripe.Webhook.construct_event`'s verification exactly.
    """
    secret = secret if secret is not None else settings.STRIPE_WEBHOOK_SECRET
    if not secret:
        # No secret configured ⇒ billing webhook is disabled; nothing can verify.
        raise StripeSignatureError("STRIPE_WEBHOOK_SECRET is not configured")
    if not sig_header:
        raise StripeSignatureError("missing Stripe-Signature header")

    timestamp, signatures = _parse_signature_header(sig_header)
    if timestamp is None or not signatures:
        raise StripeSignatureError("malformed Stripe-Signature header")

    signed_payload = b"%d." % timestamp + payload
    expected = hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeSignatureError("signature mismatch")

    tolerance = tolerance if tolerance is not None else settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS
    if tolerance and tolerance > 0:
        current = now_ts if now_ts is not None else int(time.time())
        if abs(current - timestamp) > tolerance:
            raise StripeSignatureError("timestamp outside tolerance")


def parse_event(payload: bytes, sig_header: str) -> dict:
    """Verify the signature and return the decoded event JSON object."""
    verify_signature(payload, sig_header)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StripeSignatureError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise StripeSignatureError("event payload is not a JSON object")
    return event


# ── mapping helpers ──────────────────────────────────────────────────────────


async def plan_key_for_price(session: AsyncSession, price_id: str) -> Optional[str]:
    """The plan whose `stripe_price_id` matches `price_id`, or None."""
    row = (
        await session.execute(
            select(plans.c.key).where(plans.c.stripe_price_id == price_id)
        )
    ).first()
    return row[0] if row else None


async def _workspace_for_customer(
    session: AsyncSession, customer_id: str
) -> Optional[str]:
    """The workspace already linked to this Stripe customer, or None."""
    row = (
        await session.execute(
            select(workspace_plans.c.workspace_id).where(
                workspace_plans.c.stripe_customer_id == customer_id
            )
        )
    ).first()
    return str(row[0]) if row else None


def _extract_price_id(subscription: dict) -> Optional[str]:
    """Pull the active price id out of a subscription object."""
    items = ((subscription.get("items") or {}).get("data")) or []
    if items:
        price = items[0].get("price") or {}
        if price.get("id"):
            return price["id"]
        # Legacy `plan` shape (pre-Prices API).
        plan = items[0].get("plan") or {}
        if plan.get("id"):
            return plan["id"]
    plan = subscription.get("plan") or {}
    return plan.get("id")


async def _set_stripe_ids(
    session: AsyncSession,
    workspace_id: str,
    customer_id: Optional[str],
    subscription_id: Optional[str],
) -> None:
    """Populate workspace_plans.stripe_customer_id / stripe_subscription_id.

    apply_plan has already ensured the assignment row exists, so this is a pure
    update of the billing-linkage columns on it.
    """
    values: dict[str, Any] = {}
    if customer_id is not None:
        values["stripe_customer_id"] = customer_id
    if subscription_id is not None:
        values["stripe_subscription_id"] = subscription_id
    if not values:
        return
    await session.execute(
        update(workspace_plans)
        .where(workspace_plans.c.workspace_id == workspace_id)
        .values(**values)
    )


# ── webhook event handling ───────────────────────────────────────────────────


async def handle_event(
    session: AsyncSession, event: dict, *, ip: Optional[str] = None
) -> dict:
    """Dispatch a verified Stripe event. Idempotent (apply_plan is an upsert).

    Returns a small status dict. Unhandled / unmappable events are acked with a
    `status: "ignored"` so Stripe does not retry them forever.
    """
    event_type = event.get("type", "")
    if event_type not in _SUBSCRIPTION_EVENTS:
        return {"status": "ignored", "reason": "unhandled_event_type", "event_type": event_type}

    obj = ((event.get("data") or {}).get("object")) or {}
    return await _handle_subscription(
        session, event_type, obj, event_id=event.get("id"), ip=ip
    )


async def _handle_subscription(
    session: AsyncSession,
    event_type: str,
    subscription: dict,
    *,
    event_id: Optional[str],
    ip: Optional[str],
) -> dict:
    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")
    metadata = subscription.get("metadata") or {}

    # Resolve the workspace: prefer the explicit metadata.workspace_id set at
    # checkout, else fall back to a customer we've already linked.
    workspace_id = metadata.get("workspace_id")
    if not workspace_id and customer_id:
        workspace_id = await _workspace_for_customer(session, customer_id)
    if not workspace_id:
        logger.warning(
            "stripe webhook %s: no workspace mapping (customer=%s, sub=%s)",
            event_type, customer_id, subscription_id,
        )
        return {"status": "ignored", "reason": "no_workspace_mapping"}

    # Decide the target plan.
    if event_type == "customer.subscription.deleted":
        plan_key: Optional[str] = _CANCELLED_PLAN_KEY
        price_id = None
    else:
        price_id = _extract_price_id(subscription)
        plan_key = await plan_key_for_price(session, price_id) if price_id else None
        if plan_key is None:
            logger.warning(
                "stripe webhook %s: price %s maps to no plan (ws=%s)",
                event_type, price_id, workspace_id,
            )
            return {
                "status": "ignored",
                "reason": "unknown_price",
                "price_id": price_id,
                "workspace_id": workspace_id,
            }

    # Flip the plan through the EXISTING reconciliation path, then record the
    # billing linkage — all in the caller's transaction (the route commits once).
    await plan_service.apply_plan(session, workspace_id, plan_key, _SYSTEM_ACTOR, ip=ip)
    await _set_stripe_ids(session, workspace_id, customer_id, subscription_id)

    await record_platform_event(
        session,
        actor_email=WEBHOOK_ACTOR_EMAIL,
        action="billing.subscription.synced",
        target_type="workspace",
        target_id=workspace_id,
        target_workspace_id=workspace_id,
        state_after={
            "plan_key": plan_key,
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "event_type": event_type,
        },
        ip=ip,
        metadata={
            "stripe_event_id": event_id,
            "stripe_price_id": price_id,
            "source": "stripe_webhook",
        },
    )
    return {
        "status": "applied",
        "workspace_id": workspace_id,
        "plan_key": plan_key,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
    }


# ── read-only invoices ───────────────────────────────────────────────────────


def _invoice_to_dict(inv: Any) -> dict:
    """Normalise a Stripe invoice (SDK object or plain dict) to a small shape."""
    get = inv.get if hasattr(inv, "get") else (lambda k, d=None: getattr(inv, k, d))
    return {
        "id": get("id"),
        "number": get("number"),
        "status": get("status"),
        "amount_due": get("amount_due"),
        "amount_paid": get("amount_paid"),
        "currency": get("currency"),
        "created": get("created"),  # unix seconds
        "hosted_invoice_url": get("hosted_invoice_url"),
        "invoice_pdf": get("invoice_pdf"),
    }


async def _invoice_fetcher(customer_id: str, limit: int) -> list[Any]:
    """Fetch raw invoices for a customer from Stripe.

    Lazily imports the Stripe SDK so this module imports without it; returns an
    empty list when billing is unconfigured. The blocking SDK call runs in a
    threadpool. Tests monkeypatch this function with fixtures (the SDK and the
    network are never touched in the suite).
    """
    if not settings.STRIPE_API_KEY:
        return []
    try:
        import stripe  # lazy: only needed when invoices are actually viewed
    except ImportError:  # pragma: no cover - SDK absent in some environments
        logger.warning("stripe SDK not installed; invoices unavailable")
        return []

    def _list() -> list[Any]:
        stripe.api_key = settings.STRIPE_API_KEY
        result = stripe.Invoice.list(customer=customer_id, limit=limit)
        return list(getattr(result, "data", result) or [])

    return await run_in_threadpool(_list)


async def list_invoices(
    session: AsyncSession,
    workspace_id: str,
    actor,
    *,
    limit: int = 20,
    ip: Optional[str] = None,
) -> dict:
    """Read-only: a workspace's Stripe invoices (by linked customer).

    A sensitive read, so it is audited. Returns `{customer_id, invoices}`; an
    empty list when the workspace has no Stripe customer or billing is off.
    """
    row = (
        await session.execute(
            select(workspace_plans.c.stripe_customer_id).where(
                workspace_plans.c.workspace_id == workspace_id
            )
        )
    ).first()
    customer_id = row[0] if row else None

    await record_platform_event(
        session,
        actor_email=actor.email,
        action="billing.invoices.viewed",
        target_type="workspace",
        target_id=workspace_id,
        target_workspace_id=workspace_id,
        ip=ip,
        metadata={"stripe_customer_id": customer_id},
    )

    if not customer_id:
        return {"customer_id": None, "invoices": []}

    raw = await _invoice_fetcher(customer_id, limit)
    return {"customer_id": customer_id, "invoices": [_invoice_to_dict(i) for i in raw]}
