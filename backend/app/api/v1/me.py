"""GET /v1/me — the authenticated operator's identity."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.platform_auth import PlatformActor, require_platform_admin
from app.core.platform_capabilities import ROLE_CAPABILITIES

router = APIRouter(tags=["me"])


@router.get("/me")
async def get_me(actor: PlatformActor = Depends(require_platform_admin)) -> dict:
    """Return the current operator. 403 for anyone who is not an active operator."""
    return {
        "id": actor.id,
        "email": actor.email,
        "role": actor.role,
        "capabilities": sorted(ROLE_CAPABILITIES.get(actor.role, frozenset())),
    }
