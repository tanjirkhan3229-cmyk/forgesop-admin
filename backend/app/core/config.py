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

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() in {"production", "staging"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
