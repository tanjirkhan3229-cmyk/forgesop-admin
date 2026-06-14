"""
API health & over-request endpoints (Phase 5). All behind require_platform_admin.

  * GET /v1/health             — compose the main app's /ready + rollup freshness.
  * GET /v1/metrics/api        — request volume + p50/p95/p99 + error rate,
                                 filterable by range / route / workspace.
  * GET /v1/metrics/rate-limits — top over-request offenders (route, workspace).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.platform_auth import PlatformActor, require_platform_admin
from app.services import metrics_service

router = APIRouter(tags=["metrics"])


@router.get("/health")
async def platform_health(
    _: PlatformActor = Depends(require_platform_admin),
) -> dict:
    """Operator health: the main app's /ready probe + how fresh the telemetry
    rollup is. `status` is `degraded` if the main app is not ready or the rollup
    is stale."""
    main = await metrics_service.probe_main_ready()
    redis = metrics_service.get_metrics_redis()
    try:
        rollup = await metrics_service.last_rollup_status(redis)
    finally:
        await redis.close()

    main_ok = main.get("status") in ("ready", "ok", "skipped")
    status = "ok" if (main_ok and not rollup["stale"]) else "degraded"
    return {"status": status, "main_app": main, "rollup": rollup}


@router.get("/metrics/api")
async def metrics_api(
    range: str = Query(default=metrics_service.DEFAULT_RANGE),
    route: Optional[str] = Query(default=None),
    workspace: Optional[str] = Query(default=None),
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await metrics_service.query_api_metrics(
        db, range_=range, route=route, workspace=workspace
    )


@router.get("/metrics/rate-limits")
async def metrics_rate_limits(
    range: str = Query(default=metrics_service.DEFAULT_RANGE),
    _: PlatformActor = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await metrics_service.query_rate_limit_offenders(db, range_=range)
