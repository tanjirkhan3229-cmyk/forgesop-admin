"""
Celery tasks for Phase 7 — thin sync wrappers around the async services.

All the logic lives in `alert_service` / `digest_service` (async, unit-tested
directly against a session). These tasks just open a service-role session, run
the coroutine, and commit — so the worker is a trivial, untestable shim and the
testable surface is pure service code.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app as celery
from app.core.db import SessionLocal
from app.services import alert_service, digest_service, settings_service

logger = logging.getLogger(__name__)


async def _run_sweeps() -> int:
    async with SessionLocal() as session:
        fired = await alert_service.run_sweeps(session)
        await session.commit()
        return len(fired)


async def _send_digest() -> bool:
    async with SessionLocal() as session:
        cfg = await settings_service.get_all(session)
        frequency = cfg["digest"].get("frequency", "weekly")
        # A weekly digest only goes out on Mondays; daily goes out every run.
        if frequency == "weekly" and datetime.now(tz=timezone.utc).weekday() != 0:
            return False
        result = await digest_service.send_digest(session, period=frequency)
        await session.commit()
        return result is not None


@celery.task(name="app.tasks.alerts.run_alert_sweeps")
def run_alert_sweeps() -> int:
    """Evaluate the alert sweeps; fire any tripped alerts. Returns count fired."""
    count = asyncio.run(_run_sweeps())
    logger.info("alert sweep fired %d alert(s)", count)
    return count


@celery.task(name="app.tasks.alerts.send_operator_digest")
def send_operator_digest() -> bool:
    """Send the operator digest if due (respects daily/weekly + enabled)."""
    sent = asyncio.run(_send_digest())
    logger.info("operator digest sent=%s", sent)
    return sent
