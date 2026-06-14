"""
/v1/plans — the plan catalog (gated `plans.manage`).

A plan is a bundle of public.workspaces.feature_* booleans + soft limits.
`stripe_price_id` (Phase 6) is an optional lookup key the Stripe webhook uses to
map a subscribed price → this plan; it does not affect reconciliation.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import (
    PlatformActor,
    require_platform_admin,
    require_platform_capability,
)
from app.services import plan_service

router = APIRouter(prefix="/plans", tags=["plans"])

_MANAGE = require_platform_capability("plans.manage")


class PlanCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: Optional[str] = None
    description: Optional[str] = None
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    is_public: bool = True
    sort_order: int = 0
    monthly_price_cents: Optional[int] = None
    # Phase 6: the Stripe Price this plan maps to (webhook price→plan lookup key).
    stripe_price_id: Optional[str] = None


class PlanPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    feature_flags: Optional[dict[str, bool]] = None
    limits: Optional[dict[str, Any]] = None
    is_public: Optional[bool] = None
    sort_order: Optional[int] = None
    monthly_price_cents: Optional[int] = None
    stripe_price_id: Optional[str] = None


@router.get("")
async def list_plans(
    _: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await plan_service.list_plans(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: PlanCreate,
    request: Request,
    actor: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    plan = await plan_service.create_plan(db, body.model_dump(), actor, ip=ip)
    await db.commit()
    return plan


@router.patch("/{key}")
async def patch_plan(
    key: str,
    body: PlanPatch,
    request: Request,
    actor: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    plan = await plan_service.update_plan(
        db, key, body.model_dump(exclude_unset=True), actor, ip=ip
    )
    await db.commit()
    return plan
