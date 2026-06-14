"""
Celery application for the admin service.

Standalone: its OWN broker + result backend on the console's OWN Redis, pinned
to a SEPARATE logical DB index (settings.CELERY_DB_INDEX) so it never collides
with the tenant app's queues. The beat schedule lives here; the task bodies are
in footprint_tasks.py. UTC throughout (the rollups are calendar-day based).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "forgesop_admin",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.footprint_tasks"],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
    result_expires=3600,
)

# Daily beat: roll the footprint snapshot just after midnight UTC, then derive
# the signup funnel. Times are staggered so the two don't contend for the DB.
celery_app.conf.beat_schedule = {
    "customer-footprint-rollup-daily": {
        "task": "footprint.customer_footprint_rollup",
        "schedule": crontab(hour=0, minute=15),
    },
    "signup-funnel-rollup-daily": {
        "task": "footprint.signup_funnel_rollup",
        "schedule": crontab(hour=0, minute=30),
    },
}

__all__ = ["celery_app"]
