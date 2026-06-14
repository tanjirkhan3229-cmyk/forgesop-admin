"""GET /v1/overview and GET /v1/signups — the read-only cockpit top line."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import PlatformActor, require_platform_admin
from app.services import tenant_directory

router = APIRouter(tags=["overview"])


@router.get("/overview")
async def get_overview(
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await tenant_directory.overview_kpis(db)


@router.get("/signups")
async def get_signups(
    range: str = Query(default=tenant_directory.DEFAULT_RANGE),
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await tenant_directory.signup_series(db, range)
