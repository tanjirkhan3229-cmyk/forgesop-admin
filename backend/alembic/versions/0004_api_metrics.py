"""platform: api_request_metrics + rate_limit_events (Phase 5)

Revision ID: 0004_api_metrics
Revises: 0003_footprints
Create Date: 2026-06-14

Phase 5 — API health & over-request telemetry. The sop-hub telemetry shim
(touch-point #2) writes per-route counters + latency reservoirs and per-429
events to a SHARED Redis; the admin service's `platform_metrics_rollup` beat job
(every 60s) drains them into these two tables. Touches the `platform` schema
ONLY (Architecture §5 / §11); no DDL against `public`.

Retention / TTL plan (90 days)
------------------------------
These tables grow ~per-minute-per-route, so they are the first real candidates
for partitioning + TTL in this schema. The intended operational plan:

  * `api_request_metrics`: convert to a RANGE partition on `bucket_start`
    (monthly partitions), and DROP partitions older than 90 days via a nightly
    Celery sweep (a Phase-7 "alerts & digests" style job). Until partitioning
    lands, a nightly `DELETE FROM platform.api_request_metrics WHERE bucket_start
    < now() - interval '90 days'` keeps it bounded.
  * `rate_limit_events`: same 90-day horizon, partitioned/swept on `ts`.

This migration creates the plain (non-partitioned) tables + the indexes the
dashboards query by; the partition conversion is a follow-up so this PR stays a
single, reversible additive step.
"""

from __future__ import annotations

from alembic import op

revision = "0004_api_metrics"
down_revision = "0003_footprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.api_request_metrics (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            route          text NOT NULL,
            method         text NOT NULL,
            status_class   text NOT NULL,
            workspace_id   uuid,
            bucket_start   timestamptz NOT NULL,
            bucket_seconds integer NOT NULL DEFAULT 60,
            count          integer NOT NULL DEFAULT 0,
            error_count    integer NOT NULL DEFAULT 0,
            p50_ms         integer NOT NULL DEFAULT 0,
            p95_ms         integer NOT NULL DEFAULT 0,
            p99_ms         integer NOT NULL DEFAULT 0
        )
        """
    )
    # Dashboards filter/scan by time then route; this composite serves the
    # range + per-route queries (api-metrics dashboard).
    op.execute(
        "CREATE INDEX ix_api_request_metrics_bucket_route "
        "ON platform.api_request_metrics (bucket_start, route)"
    )
    # The rollup drains one row per (route, status_class, bucket_start[, ws]);
    # this unique guard makes the drain idempotent (delete-then-insert can't
    # duplicate a bucket if a previous run half-completed).
    op.execute(
        "CREATE UNIQUE INDEX ux_api_request_metrics_bucket "
        "ON platform.api_request_metrics "
        "(route, method, status_class, bucket_start, COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid))"
    )

    op.execute(
        """
        CREATE TABLE platform.rate_limit_events (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            ts            timestamptz NOT NULL,
            rate_key      text,
            workspace_id  uuid,
            route         text,
            limit_str     text
        )
        """
    )
    # Offender ranking is "recent events grouped by route / workspace".
    op.execute(
        "CREATE INDEX ix_rate_limit_events_ts_workspace "
        "ON platform.rate_limit_events (ts, workspace_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.rate_limit_events")
    op.execute("DROP TABLE IF EXISTS platform.api_request_metrics")
