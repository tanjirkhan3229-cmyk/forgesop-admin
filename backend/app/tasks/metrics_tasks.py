"""
Scheduled API-telemetry rollup (Celery task body, Phase 5).

`platform_metrics_rollup` runs every 60s on the admin beat, draining the SHARED
Redis `platform:metrics:*` counters + `platform:ratelimit:events` into
`platform.api_request_metrics` / `platform.rate_limit_events`. The async drain
logic lives in `metrics_service.run_metrics_rollup` (unit-tested directly against
a session + a fake Redis); this wrapper only owns the session + Redis lifecycle.
"""

from __future__ import annotations

import asyncio

from app.services import metrics_service
from app.tasks.celery_app import celery_app


async def _rollup() -> dict:
    # Lazy imports so importing this module never opens a DB/Redis connection
    # (keeps `celery -A app.tasks` import-safe without live infra).
    from app.core.db import SessionLocal

    redis = metrics_service.get_metrics_redis()
    try:
        async with SessionLocal() as session:
            result = await metrics_service.run_metrics_rollup(session, redis)
            await session.commit()
    finally:
        await redis.close()
    return result


@celery_app.task(name="metrics.platform_metrics_rollup")
def platform_metrics_rollup() -> dict:
    """Drain one batch of completed Redis buckets + 429 events into Postgres."""
    return asyncio.run(_rollup())
