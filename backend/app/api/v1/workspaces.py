"""GET /v1/workspaces and GET /v1/workspaces/{id} — cross-tenant directory."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import (
    PlatformActor,
    require_platform_admin,
    require_platform_capability,
)
from app.services import plan_service, tenant_directory

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_MANAGE_WORKSPACE = require_platform_capability("workspace.manage")


class WorkspacePatch(BaseModel):
    """Apply a plan and/or set one-off flag/limit overrides.

    `plan_key` (if present) applies a plan — reconciling feature_* columns.
    `flags` / `limits` (if present) set overrides without changing the plan.
    """

    plan_key: Optional[str] = None
    flags: Optional[dict[str, bool]] = None
    limits: Optional[dict[str, Any]] = None


@router.get("")
async def list_workspaces(
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await tenant_directory.list_workspaces(
        db, search=search, page=page, page_size=page_size
    )


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ws = await tenant_directory.get_workspace(db, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace not found")
    return ws


@router.patch("/{workspace_id}")
async def patch_workspace(
    workspace_id: str,
    body: WorkspacePatch,
    request: Request,
    actor: PlatformActor = Depends(_MANAGE_WORKSPACE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply a plan and/or set flag/limit overrides — every flag flip goes
    through plan_service (transactional + audited)."""
    ip = request.client.host if request.client else None
    if body.plan_key is not None:
        await plan_service.apply_plan(db, workspace_id, body.plan_key, actor, ip=ip)
    if body.flags or body.limits:
        await plan_service.set_overrides(
            db, workspace_id, actor, flags=body.flags, limits=body.limits, ip=ip
        )
    if body.plan_key is None and not body.flags and not body.limits:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="nothing to do: provide plan_key and/or flags/limits",
        )
    await db.commit()
    ws = await tenant_directory.get_workspace(db, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace not found")
    return ws
