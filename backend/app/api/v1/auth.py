"""
/v1/auth — local email+password login for operators (PLATFORM_LOCAL_AUTH mode).

These are the only unauthenticated operator routes (you can't have a session
token before you log in). They are inert unless PLATFORM_LOCAL_AUTH is set —
returning 404 otherwise — so an IdP deployment never exposes them.

Flow (matches the SPA):
  1. POST /login {email, password}
       - unknown/inactive/bad password → 401 (generic)
       - known admin with no password yet → 200 {"status":"password_set_required"}
       - valid → 200 {"status":"ok","token":...}
  2. POST /set-password {email, password}   (first login only)
       - sets the password, then the SPA logs out and signs in via /login
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.services import auth_service
from app.services.auth_service import MIN_PASSWORD_LEN
from app.services.platform_audit import record_platform_event

router = APIRouter(prefix="/auth", tags=["auth"])


def _ensure_local_auth() -> None:
    # When an external IdP is the auth source, these routes don't exist.
    if not settings.PLATFORM_LOCAL_AUTH:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")


class LoginBody(BaseModel):
    email: EmailStr
    password: str = ""


class SetPasswordBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=256)
    setup_token: Optional[str] = None


_BAD_CREDS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
)


@router.post("/login")
async def login(
    body: LoginBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_local_auth()
    result = await auth_service.authenticate(db, str(body.email), body.password)

    if result["status"] == "password_set_required":
        return {"status": "password_set_required", "email": str(body.email).lower()}
    if result["status"] != "ok":
        raise _BAD_CREDS

    ip = request.client.host if request.client else None
    await record_platform_event(
        db,
        actor_email=result["email"],
        action="auth.login",
        target_type="platform_admin",
        target_id=result["id"],
        ip=ip,
    )
    await db.commit()
    return {
        "status": "ok",
        "token": result["token"],
        "token_type": "bearer",
        "expires_in": settings.PLATFORM_SESSION_TTL_HOURS * 3600,
    }


@router.post("/set-password")
async def set_password(
    body: SetPasswordBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_local_auth()
    result = await auth_service.set_initial_password(
        db, str(body.email), body.password, setup_token=body.setup_token
    )

    status_ = result["status"]
    if status_ == "ok":
        ip = request.client.host if request.client else None
        await record_platform_event(
            db,
            actor_email=result["email"],
            action="auth.password_set",
            target_type="platform_admin",
            target_id=result["id"],
            ip=ip,
        )
        await db.commit()
        return {"status": "ok"}

    # Map the failure reasons to HTTP without leaking more than needed.
    if status_ == "already_set":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="password already set")
    if status_ == "setup_token_invalid":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid setup token")
    if status_ == "weak":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"password must be at least {MIN_PASSWORD_LEN} characters",
        )
    raise _BAD_CREDS  # unknown/inactive email → generic
