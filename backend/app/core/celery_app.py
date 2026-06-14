"""
Celery app + beat schedule for the admin service (Phase 7).

This is the admin console's OWN Celery/Redis (separate from the tenant app). It
drives two periodic jobs:
  * `run-alert-sweeps`  — every ALERT_SWEEP_INTERVAL_MINUTES, evaluate the
    threshold sweeps and fire any tripped alerts.
  * `send-operator-digest` — daily at DIGEST_HOUR_UTC; the task itself respects
    the operator's daily/weekly preference (a weekly digest only sends on
    Mondays).

The FastAPI process never imports this module, so the API boots without a
broker. The worker/beat are launched separately:
    celery -A app.core.celery_app.celery worker --beat
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery = Celery(
    "forgesop_admin",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=["app.tasks.alerts"],
)

celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "run-alert-sweeps": {
            "task": "app.tasks.alerts.run_alert_sweeps",
            "schedule": crontab(minute=f"*/{max(1, settings.ALERT_SWEEP_INTERVAL_MINUTES)}"),
        },
        "send-operator-digest": {
            "task": "app.tasks.alerts.send_operator_digest",
            "schedule": crontab(minute=0, hour=settings.DIGEST_HOUR_UTC),
        },
    },
)
