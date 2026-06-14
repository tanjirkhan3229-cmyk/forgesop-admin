"""
/v1/settings — operator-tunable alert thresholds, digest config, recipients.

Gated `platform_settings.manage` (PLATFORM_ADMIN only). GET returns the merged
settings (defaults + stored); PUT deep-merges a partial update and audits it.
The internal `_alert_state` key is never read or written here.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import PlatformActor, require_platform_capability
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])

_MANAGE = require_platform_capability("platform_settings.manage")


class AlertThresholds(BaseModel):
    signup_drop_pct: Optional[float] = Field(default=None, ge=0, le=100)
    signup_window_days: Optional[int] = Field(default=None, ge=1, le=90)
    signup_min_baseline: Optional[int] = Field(default=None, ge=0)
    over_seat_limit_enabled: Optional[bool] = None
    error_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)
    alert_cooldown_hours: Optional[float] = Field(default=None, ge=0)


class DigestConfig(BaseModel):
    enabled: Optional[bool] = None
    frequency: Optional[str] = Field(default=None, pattern="^(daily|weekly)$")


class SettingsPatch(BaseModel):
    """Partial update — any omitted key/field is left unchanged (deep-merged)."""

    alert_thresholds: Optional[AlertThresholds] = None
    digest: Optional[DigestConfig] = None
    recipients: Optional[list[EmailStr]] = None


@router.get("")
async def get_settings(
    _: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await settings_service.get_all(db)


@router.put("")
async def put_settings(
    body: SettingsPatch,
    request: Request,
    actor: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    updates: dict[str, Any] = {}
    if body.alert_thresholds is not None:
        updates["alert_thresholds"] = body.alert_thresholds.model_dump(exclude_none=True)
    if body.digest is not None:
        updates["digest"] = body.digest.model_dump(exclude_none=True)
    if body.recipients is not None:
        updates["recipients"] = [str(e) for e in body.recipients]

    result = await settings_service.set_values(db, updates, actor, ip=ip)
    await db.commit()
    return result
