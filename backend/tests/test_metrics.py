"""
Phase 5 acceptance tests — API health & over-request telemetry.

Covers the acceptance criteria:
  * running platform_metrics_rollup against seeded Redis buckets populates
    api_request_metrics with correct counts + percentiles (and only drains
    COMPLETED minutes);
  * a simulated 429 (one entry on the shared events list) yields exactly one
    rate_limit_events row;
  * /v1/metrics/rate-limits ranks routes/tenants by 429 count;
  * the endpoints are operator-gated; /v1/health composes the main /ready +
    rollup freshness.

No real Redis: a FakeRedis (async, decode_responses semantics) is seeded in the
exact key shapes the sop-hub shim writes.
"""

from __future__ import annotations

import json
import time

import pytest

from app.models.tables import api_request_metrics, rate_limit_events
from app.services import metrics_service
from tests.conftest import make_token

OPS = "ops@forgesop.test"
ROUTE = "/api/v1/things/{thing_id}"


def _auth(email: str = OPS) -> dict:
    return {"Authorization": f"Bearer {make_token(email=email)}"}


# ── fake shared Redis ────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal async Redis with decode_responses semantics (returns strings)."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value):
        self.kv[key] = str(value)

    async def keys(self, pattern):
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        names = set(self.kv) | set(self.lists)
        return [k for k in names if k.startswith(prefix)]

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.lists.pop(k, None)

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        if end == -1:
            end = len(lst) - 1
        return lst[start:end + 1]

    async def ltrim(self, key, start, end):
        lst = self.lists.get(key, [])
        if end == -1:
            end = len(lst) - 1
        self.lists[key] = lst[start:end + 1]

    async def close(self):
        pass

    # seeding helpers
    def seed_count(self, route, status_class, minute, count):
        self.kv[f"platform:metrics:{route}:{status_class}:{minute}:count"] = str(count)

    def seed_latency(self, route, status_class, minute, samples):
        self.lists[f"platform:metrics:{route}:{status_class}:{minute}:lat_ms"] = [
            str(s) for s in samples
        ]

    def push_429(self, **event):
        self.lists.setdefault("platform:ratelimit:events", []).append(json.dumps(event))


# ── percentile unit ──────────────────────────────────────────────────────────


def test_nearest_rank_percentile():
    vals = sorted([10, 20, 30, 40])
    assert metrics_service._nearest_rank_percentile(vals, 50) == 20
    assert metrics_service._nearest_rank_percentile(vals, 95) == 40
    assert metrics_service._nearest_rank_percentile(vals, 99) == 40
    assert metrics_service._nearest_rank_percentile([], 95) == 0


# ── rollup: counts + percentiles + completed-minute-only ─────────────────────


async def test_rollup_populates_metrics_with_counts_and_percentiles(db_session):
    redis = FakeRedis()
    now_ts = time.time()
    minute = int(now_ts // 60) - 1            # a COMPLETED minute
    current = int(now_ts // 60)               # in-flight — must be skipped

    redis.seed_count(ROUTE, "2xx", minute, 4)
    redis.seed_latency(ROUTE, "2xx", minute, [10, 20, 30, 40])
    redis.seed_count(ROUTE, "5xx", minute, 1)
    redis.seed_latency(ROUTE, "5xx", minute, [500])
    # in-flight current-minute bucket — should NOT be drained yet
    redis.seed_count(ROUTE, "2xx", current, 99)

    result = await metrics_service.run_metrics_rollup(db_session, redis, now=now_ts)
    await db_session.commit()
    assert result["metric_rows"] == 2

    rows = (await db_session.execute(api_request_metrics.select())).mappings().all()
    by_class = {r["status_class"]: r for r in rows}
    assert set(by_class) == {"2xx", "5xx"}

    ok = by_class["2xx"]
    assert ok["count"] == 4
    assert ok["error_count"] == 0
    assert (ok["p50_ms"], ok["p95_ms"], ok["p99_ms"]) == (20, 40, 40)
    assert ok["bucket_seconds"] == 60

    err = by_class["5xx"]
    assert err["count"] == 1
    assert err["error_count"] == 1            # 5xx counts as an error
    assert err["p50_ms"] == 500

    # the in-flight current-minute key is untouched (not drained)
    assert f"platform:metrics:{ROUTE}:2xx:{current}:count" in redis.kv
    # the completed-minute keys were consumed
    assert f"platform:metrics:{ROUTE}:2xx:{minute}:count" not in redis.kv

    # rerunning drains nothing new (idempotent — keys already consumed)
    again = await metrics_service.run_metrics_rollup(db_session, redis, now=now_ts)
    await db_session.commit()
    assert again["metric_rows"] == 0


# ── rollup: a simulated 429 -> one rate_limit_events row ─────────────────────


async def test_simulated_429_yields_one_event_row(db_session):
    redis = FakeRedis()
    redis.push_429(
        ts="2026-06-14T12:00:00+00:00",
        rate_key="user:abc",
        workspace_id=None,
        route=ROUTE,
        limit_str="60/minute",
    )

    result = await metrics_service.run_metrics_rollup(db_session, redis, now=time.time())
    await db_session.commit()
    assert result["rate_limit_rows"] == 1

    rows = (await db_session.execute(rate_limit_events.select())).mappings().all()
    assert len(rows) == 1
    assert rows[0]["rate_key"] == "user:abc"
    assert rows[0]["route"] == ROUTE
    assert rows[0]["limit_str"] == "60/minute"

    # consumed from the list; a rerun adds nothing
    again = await metrics_service.run_metrics_rollup(db_session, redis, now=time.time())
    await db_session.commit()
    assert again["rate_limit_rows"] == 0


# ── offender ranking ─────────────────────────────────────────────────────────


async def test_rate_limit_offenders_rank_routes_and_tenants(db_session):
    redis = FakeRedis()
    ws_a, ws_b = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    # ws_a hammers /things 3x; ws_b trips /widgets once
    for _ in range(3):
        redis.push_429(ts="2026-06-14T12:00:00+00:00", rate_key="user:a", workspace_id=ws_a, route=ROUTE, limit_str="60/minute")
    redis.push_429(ts="2026-06-14T12:00:00+00:00", rate_key="user:b", workspace_id=ws_b, route="/api/v1/widgets", limit_str="60/minute")

    await metrics_service.run_metrics_rollup(db_session, redis, now=time.time())
    await db_session.commit()

    out = await metrics_service.query_rate_limit_offenders(db_session, range_="24h")
    assert out["total"] == 4
    # top (route, workspace) offender is ws_a on /things with 3
    assert out["offenders"][0] == {"route": ROUTE, "workspace_id": ws_a, "count": 3}
    assert out["by_route"][0] == {"route": ROUTE, "count": 3}
    assert out["by_workspace"][0]["workspace_id"] == ws_a
    assert out["by_workspace"][0]["count"] == 3


# ── endpoints ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/v1/metrics/api", "/v1/metrics/rate-limits", "/v1/health"])
async def test_metrics_endpoints_require_operator(client, seed_admins, path):
    assert (await client.get(path)).status_code == 403


async def test_metrics_api_endpoint_returns_by_route(client, seed_admins, db_session):
    redis = FakeRedis()
    now_ts = time.time()
    minute = int(now_ts // 60) - 1
    redis.seed_count(ROUTE, "2xx", minute, 8)
    redis.seed_latency(ROUTE, "2xx", minute, [5, 15, 25, 35, 45, 55, 65, 75])
    redis.seed_count(ROUTE, "5xx", minute, 2)
    redis.seed_latency(ROUTE, "5xx", minute, [100, 200])
    await metrics_service.run_metrics_rollup(db_session, redis, now=now_ts)
    await db_session.commit()

    resp = await client.get("/v1/metrics/api?range=1h", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["series"]) == 2
    route_summary = {a["route"]: a for a in body["by_route"]}
    agg = route_summary[ROUTE]
    assert agg["count"] == 10          # 8 + 2
    assert agg["error_count"] == 2     # the 5xx bucket
    assert agg["error_rate"] == 0.2


async def test_rate_limits_endpoint(client, seed_admins, db_session):
    redis = FakeRedis()
    redis.push_429(ts="2026-06-14T12:00:00+00:00", rate_key="user:a", workspace_id=None, route=ROUTE, limit_str="60/minute")
    await metrics_service.run_metrics_rollup(db_session, redis, now=time.time())
    await db_session.commit()

    resp = await client.get("/v1/metrics/rate-limits?range=24h", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_health_composes_main_ready_and_rollup_freshness(client, seed_admins, monkeypatch):
    async def _fake_main():
        return {"status": "ready", "checks": {"postgres": {"status": "ok"}}}

    fresh = FakeRedis()
    await fresh.set("platform:metrics:rollup:last_run", metrics_service._now().isoformat())

    monkeypatch.setattr(metrics_service, "probe_main_ready", _fake_main)
    monkeypatch.setattr(metrics_service, "get_metrics_redis", lambda: fresh)

    resp = await client.get("/v1/health", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["main_app"]["status"] == "ready"
    assert body["rollup"]["stale"] is False


async def test_health_degraded_when_rollup_stale(client, seed_admins, monkeypatch):
    async def _fake_main():
        return {"status": "ready"}

    stale = FakeRedis()  # no heartbeat set -> stale

    monkeypatch.setattr(metrics_service, "probe_main_ready", _fake_main)
    monkeypatch.setattr(metrics_service, "get_metrics_redis", lambda: stale)

    resp = await client.get("/v1/health", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["rollup"]["stale"] is True
