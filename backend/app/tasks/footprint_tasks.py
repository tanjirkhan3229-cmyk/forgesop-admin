"""
Scheduled footprint rollups (Celery task bodies).

Celery tasks are synchronous; the service layer is async. Each task opens its own
service-role `AsyncSession`, runs the corresponding `footprint_service` coroutine,
COMMITS, and disposes. The async heavy lifting lives in footprint_service — these
wrappers only handle scheduling, session lifecycle, and the day argument — so the
exact same rollup logic is unit-tested directly against a session (test_footprint.py)
without a broker or worker.

By default a run snapshots the CURRENT UTC day. Pass an ISO `day` ("2026-06-14")
to (re)compute a specific day; the footprint rollup is idempotent per day.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Optional

from app.services import footprint_service
from app.tasks.celery_app import celery_app


def _today() -> date:
    return datetime.now(tz=timezone.utc).date()


def _resolve_day(day: Optional[str]) -> date:
    return date.fromisoformat(day) if day else _today()


async def _footprint_rollup(day: date) -> int:
    # Imported lazily so importing this module never opens a DB connection
    # (keeps `celery -A app.tasks` import-safe without a live DB).
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        written = await footprint_service.run_footprint_rollup(session, day)
        await session.commit()
    return written


async def _signup_rollup(day: Optional[date]) -> int:
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        inserted = await footprint_service.run_signup_funnel_rollup(session, day)
        await session.commit()
    return inserted


@celery_app.task(name="footprint.customer_footprint_rollup")
def customer_footprint_rollup(day: Optional[str] = None) -> dict:
    """Compute + upsert one footprint snapshot per workspace for `day`
    (default: today UTC). Idempotent per day."""
    resolved = _resolve_day(day)
    written = asyncio.run(_footprint_rollup(resolved))
    return {"day": resolved.isoformat(), "rows_written": written}


@celery_app.task(name="footprint.signup_funnel_rollup")
def signup_funnel_rollup(day: Optional[str] = None) -> dict:
    """Backfill signup_events from public.users (idempotent by user). With no
    `day`, considers all users; with a `day`, only that UTC day's signups."""
    resolved = date.fromisoformat(day) if day else None
    inserted = asyncio.run(_signup_rollup(resolved))
    return {"day": resolved.isoformat() if resolved else None, "events_inserted": inserted}
