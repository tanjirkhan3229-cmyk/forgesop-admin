"""
/v1/footprints — the customer footprint directory + per-tenant detail (Phase 3).

Read-only operator endpoints (behind `require_platform_admin`, like the rest of
the read cockpit). The snapshots themselves are produced by the admin service's
own Celery beat (tasks/footprint_tasks.py); these endpoints only read
`platform.customer_footprint_daily` and join the live plan seat limit in.

  * GET /v1/footprints              — sortable directory with the
                                      "over seat limit" / "inactive >= N days" chips.
  * GET /v1/footprints/{workspace}  — detail + a daily usage trend series.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import PlatformActor, require_platform_admin
from app.services import footprint_service

router = APIRouter(prefix="/footprints", tags=["footprints"])


@router.get("")
async def list_footprints(
    search: Optional[str] = Query(default=None),
    over_seat_limit: bool = Query(default=False, description="Only workspaces whose seats_used exceeds the plan seat limit."),
    inactive_days: Optional[int] = Query(
        default=None,
        ge=0,
        description="Only workspaces with no activity for at least this many days.",
    ),
    sort: str = Query(default=footprint_service.DEFAULT_SORT),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await footprint_service.list_footprints(
        db,
        search=search,
        over_seat_limit=over_seat_limit,
        inactive_days=inactive_days,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )


@router.get("/{workspace_id}")
async def get_footprint(
    workspace_id: str,
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    detail = await footprint_service.get_footprint_detail(db, workspace_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace not found")
    return detail
