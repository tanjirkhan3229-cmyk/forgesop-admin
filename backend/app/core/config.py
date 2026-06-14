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

    # ── Operator identity (distinct issuer/audience from the tenant project) ─
    # A tenant Supabase JWT must NEVER satisfy the platform gate, so the
    # operator IdP uses its own issuer + audience + JWKS. verify_platform_token
    # REQUIRES both to match.
    PLATFORM_JWT_ISSUER: Optional[str] = None
    PLATFORM_JWT_AUDIENCE: Optional[str] = None
    PLATFORM_JWKS_URL: Optional[str] = None

    # CORS — the admin SPA origin (e.g. https://admin.forgesop.app).
    ADMIN_ORIGIN: str = "http://localhost:5173"

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

    # Footprints: how many trailing days of snapshots the detail trend returns.
    FOOTPRINT_TREND_DAYS: int = 30
    # Default "inactive >= N days" threshold the directory filter uses when the
    # client does not pass one.
    FOOTPRINT_INACTIVE_DAYS_DEFAULT: int = 14

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
