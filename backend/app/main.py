"""
ForgeSOP Platform Admin Console — FastAPI entrypoint.

Standalone operator service. It shares the sop-hub Supabase database but is a
SEPARATE process on its own origin; no tenant token can reach it (every route
is behind `require_platform_admin`). See CLAUDE.md.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    admins,
    auth,
    billing,
    me,
    overview,
    plans,
    settings as settings_api,
    users,
    workspaces,
)
from app.core.config import settings

app = FastAPI(
    title="ForgeSOP Platform Admin Console",
    version="0.1.0",
    # Hide schema in production; the surface is operators-only either way.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ADMIN_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_V1 = "/v1"
app.include_router(auth.router, prefix=API_V1)
app.include_router(me.router, prefix=API_V1)
app.include_router(admins.router, prefix=API_V1)
app.include_router(overview.router, prefix=API_V1)
app.include_router(workspaces.router, prefix=API_V1)
app.include_router(users.router, prefix=API_V1)
app.include_router(plans.router, prefix=API_V1)
app.include_router(billing.router, prefix=API_V1)
app.include_router(settings_api.router, prefix=API_V1)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Liveness stub (open). A real /ready probe is a Phase-5 concern."""
    return {"status": "ok"}
