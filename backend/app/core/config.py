"""
Settings for the ForgeSOP Platform Admin Console.

Standalone operator service. NOT part of the sop-hub app — see CLAUDE.md.
All values are read from the environment (see .env.example). The service
connects to the SHARED Supabase Postgres database as the SERVICE-ROLE
Postgres role and owns the `platform` schema only.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"

    # ── Shared Supabase Postgres (service-role) ──────────────────────────
    # The console connects with the SERVICE-ROLE Postgres role so it can
    # read public.* across tenants (RLS-bypassing). search_path is pinned
    # to `platform,public` by core/db.py. Async driver (asyncpg) expected:
    #   postgresql+asyncpg://service_role:...@db.<ref>.supabase.co:5432/postgres
    DATABASE_URL: str = "sqlite+aiosqlite://"

    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None

    # Verify the DB server's TLS cert against a CA. Default ON (secure). The
    # Supabase *pooler* presents a self-signed cert chain, so set this False to
    # connect (still encrypted, just not CA-verified). To "tighten later":
    # download Supabase's CA cert and re-enable verification.
    DB_SSL_VERIFY: bool = True

    # ── Operator identity (distinct issuer/audience from the tenant project) ─
    # A tenant Supabase JWT must NEVER satisfy the platform gate, so the
    # operator IdP uses its own issuer + audience + JWKS. verify_platform_token
    # REQUIRES both to match.
    PLATFORM_JWT_ISSUER: Optional[str] = None
    PLATFORM_JWT_AUDIENCE: Optional[str] = None
    PLATFORM_JWKS_URL: Optional[str] = None

    # ── Local operator auth (self-contained email+password) ──────────────────
    # Alternative to an external IdP for small teams: the console stores PBKDF2
    # password hashes and issues its own HS256 session tokens. When True,
    # require_platform_admin verifies console session tokens instead of IdP JWTs.
    # Less secure than SSO+MFA on a cross-tenant surface — intended as a
    # bootstrap; migrate to an IdP for production hardening.
    PLATFORM_LOCAL_AUTH: bool = False
    # Server-only HS256 signing secret for console session tokens. REQUIRED when
    # PLATFORM_LOCAL_AUTH is True. Never leaves this service.
    PLATFORM_SESSION_SECRET: Optional[str] = None
    PLATFORM_SESSION_TTL_HOURS: int = 12
    # Optional: if set, the first-time set-password call must present this token
    # (closes the first-login account-takeover window). Leave blank for the
    # plain email→set-password flow.
    PLATFORM_SETUP_TOKEN: Optional[str] = None

    # CORS — the admin SPA origin (e.g. https://admin.forgesop.app).
    ADMIN_ORIGIN: str = "http://localhost:5173"

    # ── Stripe billing (Phase 6 — optional) ─────────────────────────────────
    # Billing is OFF until both are set. The webhook verifies every payload's
    # `Stripe-Signature` against STRIPE_WEBHOOK_SECRET (an absent secret means
    # no event can be accepted), and the read-only invoices view calls the
    # Stripe API with STRIPE_API_KEY. The key lives only in this service's
    # environment — never in any browser, never logged. Stripe maps a
    # subscribed `stripe_price_id → platform.plans.key` and the existing
    # `plan_service.apply_plan` does the reconciliation, unchanged.
    STRIPE_API_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    # Reject events whose signature timestamp is older than this (replay guard).
    STRIPE_WEBHOOK_TOLERANCE_SECONDS: int = 300

    # ── Celery / Redis (Phase 3) ─────────────────────────────────────────
    # The console runs its OWN Celery worker + beat against its OWN Redis
    # (Architecture §3 — "its own Redis"). REDIS_URL points at that Redis
    # instance; CELERY_DB_INDEX pins a SEPARATE logical DB index so admin
    # broker/result traffic never collides with anything else on the host.
    # CELERY_BROKER_URL / CELERY_RESULT_BACKEND default to REDIS_URL + index
    # but can be overridden explicitly.
    REDIS_URL: str = "redis://localhost:6379"
    CELERY_DB_INDEX: int = 1
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None

    # Phase 7 — alert sweep cadence (minutes) + daily digest hour (UTC).
    ALERT_SWEEP_INTERVAL_MINUTES: int = 15
    DIGEST_HOUR_UTC: int = 13

    # Footprints: how many trailing days of snapshots the detail trend returns.
    FOOTPRINT_TREND_DAYS: int = 30
    # Default "inactive >= N days" threshold the directory filter uses when the
    # client does not pass one.
    FOOTPRINT_INACTIVE_DAYS_DEFAULT: int = 14

    # ── Phase 5 — API health & over-request telemetry ────────────────────
    # The sop-hub telemetry shim (touch-point #2) writes per-route counters +
    # latency reservoirs and per-429 events to a SHARED Redis under
    # `platform:metrics:*` / `platform:ratelimit:*`. METRICS_REDIS_URL points at
    # THAT Redis (the one sop-hub's app uses) — distinct from the admin's own
    # Celery broker Redis. Defaults to REDIS_URL for single-instance dev.
    METRICS_REDIS_URL: Optional[str] = None
    # Telemetry buckets are 1-minute; the rollup runs every 60s and only drains
    # COMPLETED minutes (never the in-flight current minute).
    METRICS_BUCKET_SECONDS: int = 60
    # Retention target for the rollup tables (see migration note: TTL/partition).
    METRICS_RETENTION_DAYS: int = 90
    # /v1/health flags the rollup stale if the last successful run is older.
    ROLLUP_STALE_SECONDS: int = 180
    # The main app base URL whose /ready probe /v1/health composes.
    MAIN_APP_URL: Optional[str] = None

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() in {"production", "staging"}

    @property
    def celery_broker_url(self) -> str:
        """The admin Celery broker — its own Redis on a separate DB index."""
        return self.CELERY_BROKER_URL or f"{self.REDIS_URL}/{self.CELERY_DB_INDEX}"

    @property
    def celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.celery_broker_url

    @property
    def metrics_redis_url(self) -> str:
        """The SHARED Redis the sop-hub telemetry shim writes to (drained by the
        platform_metrics_rollup). Defaults to REDIS_URL (single-instance dev)."""
        return self.METRICS_REDIS_URL or self.REDIS_URL


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
