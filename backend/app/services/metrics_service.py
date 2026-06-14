"""
API health & over-request telemetry — rollup + read queries (Phase 5).

The sop-hub telemetry shim (touch-point #2) writes to a SHARED Redis on the
request hot path (no Postgres there):

  * `platform:metrics:{route}:{status_class}:{minute}:count`   -> INCR (int)
  * `platform:metrics:{route}:{status_class}:{minute}:lat_ms`  -> LIST of ms
  * `platform:metrics:ws:{minute}:{workspace_id}`              -> INCR (auxiliary)
  * `platform:ratelimit:events`                                -> LIST of JSON 429s
      {ts, rate_key, workspace_id, route, limit_str}

`run_metrics_rollup` (the `platform_metrics_rollup` beat job, every 60s) drains
COMPLETED minute buckets into `platform.api_request_metrics` (computing
percentiles from each reservoir) and the 429 event list into
`platform.rate_limit_events`, deletes what it consumed, and stamps a heartbeat
(`platform:metrics:rollup:last_run`) that `/v1/health` reads for freshness.

The route label is always the route TEMPLATE the shim emitted (low cardinality);
this module never sees a raw URL. `workspace_id` on api_request_metrics is NULL
because the shim's per-route counters aren't workspace-scoped.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tables import api_request_metrics, rate_limit_events

_METRICS_PREFIX = "platform:metrics:"
_RATELIMIT_EVENTS_KEY = "platform:ratelimit:events"
_ROLLUP_HEARTBEAT_KEY = "platform:metrics:rollup:last_run"

# status classes that count toward `error_count` (client + server errors).
_ERROR_CLASSES = {"4xx", "5xx"}

_RANGE_SECONDS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
DEFAULT_RANGE = "1h"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _nearest_rank_percentile(sorted_vals: list[float], pct: float) -> int:
    """Nearest-rank percentile (ms, rounded to int). Empty -> 0."""
    if not sorted_vals:
        return 0
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_vals)))
    return int(round(sorted_vals[min(rank, len(sorted_vals)) - 1]))


# ── rollup (Celery beat: platform_metrics_rollup) ───────────────────────────


async def run_metrics_rollup(
    session: AsyncSession, redis, *, now: Optional[float] = None
) -> dict:
    """Drain completed Redis buckets into the rollup tables. Idempotent
    (delete-then-insert per bucket; consumed keys are removed). Does NOT commit —
    the caller owns the transaction. `redis` is decode_responses=True."""
    now_ts = now if now is not None else time.time()
    bucket_seconds = settings.METRICS_BUCKET_SECONDS
    current_minute = int(now_ts // bucket_seconds)

    metric_rows = await _drain_request_metrics(
        session, redis, current_minute=current_minute, bucket_seconds=bucket_seconds
    )
    rl_rows = await _drain_rate_limit_events(session, redis)

    # Heartbeat for /v1/health freshness (best-effort; not a hard dep).
    try:
        await redis.set(_ROLLUP_HEARTBEAT_KEY, _now().isoformat())
    except Exception:  # noqa: BLE001
        pass

    return {"metric_rows": metric_rows, "rate_limit_rows": rl_rows}


async def _scan_metric_keys(redis) -> list[str]:
    """All `platform:metrics:*` keys (works with real redis.scan_iter or a fake
    that exposes `keys`)."""
    if hasattr(redis, "scan_iter"):
        return [k async for k in redis.scan_iter(match=f"{_METRICS_PREFIX}*")]
    return await redis.keys(f"{_METRICS_PREFIX}*")


async def _drain_request_metrics(
    session: AsyncSession, redis, *, current_minute: int, bucket_seconds: int
) -> int:
    keys = await _scan_metric_keys(redis)

    # group[(route, status_class, minute)] = {"count": int, "lat": [floats]}
    groups: dict[tuple[str, str, int], dict[str, Any]] = {}
    consumed: list[str] = []

    for key in keys:
        remainder = key[len(_METRICS_PREFIX):]
        # Skip auxiliary keys (per-workspace counters, the rollup heartbeat).
        if remainder.startswith("ws:") or remainder.startswith("rollup:"):
            continue
        # `{route}:{status_class}:{minute}:{suffix}` — route has no ':' (it's a
        # path template), so rsplit from the right is unambiguous.
        parts = remainder.rsplit(":", 3)
        if len(parts) != 4:
            continue
        route, status_class, minute_str, suffix = parts
        try:
            minute = int(minute_str)
        except ValueError:
            continue
        # Never drain the in-flight current minute — it's still receiving writes.
        if minute >= current_minute:
            continue

        gkey = (route, status_class, minute)
        g = groups.setdefault(gkey, {"count": 0, "lat": []})
        if suffix == "count":
            raw = await redis.get(key)
            g["count"] = int(raw or 0)
            consumed.append(key)
        elif suffix == "lat_ms":
            vals = await redis.lrange(key, 0, -1)
            g["lat"].extend(float(v) for v in vals)
            consumed.append(key)

    written = 0
    for (route, status_class, minute), g in groups.items():
        count = int(g["count"])
        if count == 0 and not g["lat"]:
            continue
        lat_sorted = sorted(g["lat"])
        bucket_start = datetime.fromtimestamp(minute * bucket_seconds, tz=timezone.utc)
        error_count = count if status_class in _ERROR_CLASSES else 0

        # Idempotent: replace any existing row for this exact bucket.
        await session.execute(
            delete(api_request_metrics).where(
                and_(
                    api_request_metrics.c.route == route,
                    api_request_metrics.c.status_class == status_class,
                    api_request_metrics.c.bucket_start == bucket_start,
                    api_request_metrics.c.workspace_id.is_(None),
                )
            )
        )
        await session.execute(
            insert(api_request_metrics).values(
                id=str(uuid.uuid4()),
                route=route,
                method="ALL",  # the shim aggregates across methods per (route,status)
                status_class=status_class,
                workspace_id=None,
                bucket_start=bucket_start,
                bucket_seconds=bucket_seconds,
                count=count,
                error_count=error_count,
                p50_ms=_nearest_rank_percentile(lat_sorted, 50),
                p95_ms=_nearest_rank_percentile(lat_sorted, 95),
                p99_ms=_nearest_rank_percentile(lat_sorted, 99),
            )
        )
        written += 1

    # Remove only what we read (completed buckets); in-flight keys are untouched.
    for key in consumed:
        await redis.delete(key)
    return written


async def _drain_rate_limit_events(session: AsyncSession, redis) -> int:
    """Pop the 429 event list into one row each. Consumes exactly the entries
    present at read time (LTRIM the consumed prefix) so concurrent pushes during
    the drain are kept for the next run."""
    n = int(await redis.llen(_RATELIMIT_EVENTS_KEY) or 0)
    if n == 0:
        return 0
    raw_items = await redis.lrange(_RATELIMIT_EVENTS_KEY, 0, n - 1)

    written = 0
    for raw in raw_items:
        try:
            ev = json.loads(raw)
        except (TypeError, ValueError):
            continue
        ts_val = ev.get("ts")
        ts = _parse_ts(ts_val)
        await session.execute(
            insert(rate_limit_events).values(
                id=str(uuid.uuid4()),
                ts=ts,
                rate_key=ev.get("rate_key"),
                workspace_id=ev.get("workspace_id"),
                route=ev.get("route"),
                limit_str=ev.get("limit_str"),
            )
        )
        written += 1

    # Drop the consumed prefix; keep anything pushed after our snapshot.
    await redis.ltrim(_RATELIMIT_EVENTS_KEY, n, -1)
    return written


def _parse_ts(value: Any) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return _now()


# ── reads (endpoints) ────────────────────────────────────────────────────────


def _cutoff(range_: str) -> datetime:
    seconds = _RANGE_SECONDS.get(range_, _RANGE_SECONDS[DEFAULT_RANGE])
    return _now() - timedelta(seconds=seconds)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return str(value)


async def query_api_metrics(
    session: AsyncSession,
    *,
    range_: str = DEFAULT_RANGE,
    route: Optional[str] = None,
    workspace: Optional[str] = None,
) -> dict:
    """Time-series rows in range + a per-route summary (volume, error rate,
    worst-case p50/p95/p99 across the range)."""
    range_ = range_ if range_ in _RANGE_SECONDS else DEFAULT_RANGE
    m = api_request_metrics
    where = [m.c.bucket_start >= _cutoff(range_)]
    if route:
        where.append(m.c.route == route)
    if workspace:
        where.append(m.c.workspace_id == workspace)

    rows = (
        await session.execute(select(m).where(and_(*where)).order_by(m.c.bucket_start))
    ).mappings().all()

    series = [
        {
            "route": r["route"],
            "method": r["method"],
            "status_class": r["status_class"],
            "workspace_id": str(r["workspace_id"]) if r["workspace_id"] else None,
            "bucket_start": _iso(r["bucket_start"]),
            "bucket_seconds": int(r["bucket_seconds"]),
            "count": int(r["count"]),
            "error_count": int(r["error_count"]),
            "p50_ms": int(r["p50_ms"]),
            "p95_ms": int(r["p95_ms"]),
            "p99_ms": int(r["p99_ms"]),
        }
        for r in rows
    ]

    by_route: dict[str, dict[str, Any]] = {}
    for r in series:
        agg = by_route.setdefault(
            r["route"],
            {"route": r["route"], "count": 0, "error_count": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0},
        )
        agg["count"] += r["count"]
        agg["error_count"] += r["error_count"]
        # Worst-case percentile across buckets (percentiles can't be averaged).
        agg["p50_ms"] = max(agg["p50_ms"], r["p50_ms"])
        agg["p95_ms"] = max(agg["p95_ms"], r["p95_ms"])
        agg["p99_ms"] = max(agg["p99_ms"], r["p99_ms"])
    for agg in by_route.values():
        agg["error_rate"] = round(agg["error_count"] / agg["count"], 4) if agg["count"] else 0.0

    summary = sorted(by_route.values(), key=lambda a: a["count"], reverse=True)
    return {"range": range_, "series": series, "by_route": summary}


async def query_rate_limit_offenders(
    session: AsyncSession, *, range_: str = DEFAULT_RANGE
) -> dict:
    """Top over-request offenders in range: grouped by (route, workspace), and
    separately by route and by workspace, each ranked by 429 count."""
    range_ = range_ if range_ in _RANGE_SECONDS else DEFAULT_RANGE
    e = rate_limit_events
    cutoff = _cutoff(range_)

    async def _grouped(*cols) -> list[dict]:
        stmt = (
            select(*cols, func.count(e.c.id).label("count"))
            .where(e.c.ts >= cutoff)
            .group_by(*cols)
            .order_by(func.count(e.c.id).desc())
        )
        return [dict(r) for r in (await session.execute(stmt)).mappings().all()]

    pairs = await _grouped(e.c.route, e.c.workspace_id)
    offenders = [
        {
            "route": r["route"],
            "workspace_id": str(r["workspace_id"]) if r["workspace_id"] else None,
            "count": int(r["count"]),
        }
        for r in pairs
    ]
    by_route = [
        {"route": r["route"], "count": int(r["count"])}
        for r in await _grouped(e.c.route)
    ]
    by_workspace = [
        {
            "workspace_id": str(r["workspace_id"]) if r["workspace_id"] else None,
            "count": int(r["count"]),
        }
        for r in await _grouped(e.c.workspace_id)
    ]
    total = sum(o["count"] for o in offenders)
    return {
        "range": range_,
        "total": total,
        "offenders": offenders,
        "by_route": by_route,
        "by_workspace": by_workspace,
    }


def get_metrics_redis():
    """Async client for the SHARED metrics Redis (decode_responses=True). The
    rollup task and /v1/health both use this; tests monkeypatch it."""
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.metrics_redis_url, decode_responses=True)


async def probe_main_ready() -> dict:
    """GET the main app's /ready probe so /v1/health can compose it. Soft: any
    failure is reported, never raised."""
    if not settings.MAIN_APP_URL:
        return {"status": "skipped", "detail": "MAIN_APP_URL not configured"}
    url = f"{settings.MAIN_APP_URL.rstrip('/')}/ready"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {}
        return {
            "status": body.get("status") or ("ok" if resp.status_code == 200 else "down"),
            "http_status": resp.status_code,
            "checks": body.get("checks"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "detail": str(exc)[:200]}


async def last_rollup_status(redis) -> dict:
    """Freshness of the rollup heartbeat for /v1/health."""
    try:
        raw = await redis.get(_ROLLUP_HEARTBEAT_KEY)
    except Exception:  # noqa: BLE001
        raw = None
    if not raw:
        return {"last_run": None, "age_seconds": None, "stale": True}
    last = _parse_ts(raw)
    age = (_now() - last).total_seconds()
    return {
        "last_run": last.isoformat(),
        "age_seconds": round(age, 1),
        "stale": age > settings.ROLLUP_STALE_SECONDS,
    }
