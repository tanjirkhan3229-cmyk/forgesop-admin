"""
/v1/admins — manage the operator allowlist (platform.platform_admins).

PLATFORM_ADMIN only (capability `platform_admins.manage`). Every mutation is
hash-chain audited to `platform.platform_audit`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import (
    PlatformActor,
    require_platform_admin,
    require_platform_capability,
)
from app.core.platform_capabilities import PLATFORM_ROLES
from app.models.tables import platform_admins
from app.services.platform_audit import record_platform_event

router = APIRouter(prefix="/admins", tags=["admins"])

_MANAGE = require_platform_capability("platform_admins.manage")


class AdminCreate(BaseModel):
    email: EmailStr
    role: str

    @field_validator("role")
    @classmethod
    def _role_known(cls, v: str) -> str:
        if v not in PLATFORM_ROLES:
            raise ValueError(f"role must be one of {PLATFORM_ROLES}")
        return v


class AdminPatch(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def _role_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in PLATFORM_ROLES:
            raise ValueError(f"role must be one of {PLATFORM_ROLES}")
        return v


def _row_to_dict(row) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
    }


@router.get("")
async def list_admins(
    actor: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (
        await db.execute(
            select(platform_admins).order_by(platform_admins.c.email)
        )
    ).mappings().all()
    return [_row_to_dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_admin(
    body: AdminCreate,
    request: Request,
    actor: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    email = body.email.strip().lower()
    exists = (
        await db.execute(
            select(platform_admins.c.id).where(platform_admins.c.email == email)
        )
    ).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="operator already exists")

    now = datetime.now(tz=timezone.utc)
    new_id = str(uuid.uuid4())
    await db.execute(
        insert(platform_admins).values(
            id=new_id,
            email=email,
            role=body.role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    await record_platform_event(
        db,
        actor_email=actor.email,
        action="platform_admin.created",
        target_type="platform_admin",
        target_id=new_id,
        state_after={"email": email, "role": body.role, "is_active": True},
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return {"id": new_id, "email": email, "role": body.role, "is_active": True}


@router.patch("/{admin_id}")
async def patch_admin(
    admin_id: str,
    body: AdminPatch,
    request: Request,
    actor: PlatformActor = Depends(_MANAGE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await db.execute(
            select(platform_admins).where(platform_admins.c.id == admin_id)
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="operator not found")

    before = _row_to_dict(row)
    changes: dict = {}
    if body.role is not None:
        changes["role"] = body.role
    if body.is_active is not None:
        changes["is_active"] = body.is_active
    if not changes:
        return before

    changes["updated_at"] = datetime.now(tz=timezone.utc)
    await db.execute(
        update(platform_admins)
        .where(platform_admins.c.id == admin_id)
        .values(**changes)
    )
    after = {**before, **{k: v for k, v in changes.items() if k != "updated_at"}}
    await record_platform_event(
        db,
        actor_email=actor.email,
        action="platform_admin.updated",
        target_type="platform_admin",
        target_id=admin_id,
        state_before=before,
        state_after=after,
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return after
