"""
/v1/billing — Stripe webhook + read-only invoices (Phase 6).

Two endpoints with deliberately different auth models:

  * `POST /v1/billing/webhook` — called by **Stripe's servers**, not a browser
    or an operator. It carries no operator token; its authenticity is proven by
    the `Stripe-Signature` HMAC (verified against STRIPE_WEBHOOK_SECRET). A bad
    or absent signature → **400**. The verified event flips the workspace's plan
    via the existing `plan_service.apply_plan` (see billing_service).

  * `GET /v1/billing/invoices` — an operator read, gated by `tenant.read`
    (held by every operator role). Read-only; audited as a sensitive read.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import PlatformActor, require_platform_capability
from app.services import billing_service

router = APIRouter(prefix="/billing", tags=["billing"])

_READ = require_platform_capability("tenant.read")


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Signature-verified Stripe webhook. No operator token — Stripe calls it."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = billing_service.parse_event(payload, sig_header)
    except billing_service.StripeSignatureError:
        # Never reveal why; a forged/replayed payload just gets a flat 400.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid stripe signature",
        )

    ip = request.client.host if request.client else None
    result = await billing_service.handle_event(db, event, ip=ip)
    await db.commit()
    return result


@router.get("/invoices")
async def list_invoices(
    request: Request,
    workspace_id: str = Query(..., description="Workspace to list invoices for"),
    actor: PlatformActor = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Read-only Stripe invoices for a workspace (by its linked customer)."""
    ip = request.client.host if request.client else None
    result = await billing_service.list_invoices(db, workspace_id, actor, ip=ip)
    await db.commit()  # commit the sensitive-read audit row
    return result
