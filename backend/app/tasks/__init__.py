"""Celery app + scheduled rollups for the admin service (Phase 3).

The console runs its OWN Celery worker + beat against its OWN Redis (a separate
DB index — see core/config). Importing this package gives you the configured
`celery_app` with the daily footprint + signup-funnel rollups registered on beat.

    celery -A app.tasks worker --beat --loglevel=info
"""

from __future__ import annotations

from app.tasks.celery_app import celery_app

# Importing the tasks module registers the @celery_app.task functions and the
# beat schedule onto celery_app.
from app.tasks import alerts  # noqa: E402,F401
from app.tasks import footprint_tasks  # noqa: E402,F401

__all__ = ["celery_app"]
