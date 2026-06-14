"""GET /v1/users — cross-tenant user directory."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import PlatformActor, require_platform_admin
from app.services import tenant_directory

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(
    search: Optional[str] = Query(default=None),
    workspace_id: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await tenant_directory.list_users(
        db,
        search=search,
        workspace_id=workspace_id,
        role=role,
        status=status,
        page=page,
        page_size=page_size,
    )
