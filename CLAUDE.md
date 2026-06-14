# CLAUDE.md

Guidance for Claude Code when working in **this** repository.

## What this is (and is NOT)

This is the **ForgeSOP Platform Admin Console** — a **standalone, cross-tenant
operator console** for ForgeSOP staff. It is **NOT part of the sop-hub product
app.** It is a separate service in its own repo, on its own origin, behind
SSO/MFA, that ships nothing into any tenant's browser bundle.

- **Tenant admin** (lives in sop-hub) = a workspace owner administering *their
  own* workspace; RLS-scoped to one `workspace_id`. Not this.
- **Platform / operator console** (this repo) = *ForgeSOP staff* looking *across
  all tenants*; **bypasses RLS**; **must never be reachable by a tenant**.

Source-of-truth design docs live in the sop-hub repo:
`ForgeSOP_Admin_Panel_Architecture.md` (rationale: §3 topology, §4 security,
§5 data model), `ForgeSOP_Admin_Panel_Build_Plan.md` (phases), and
`ForgeSOP_Admin_Panel_Build_Prompts.md` (paste-ready prompts). The reference
implementations we **copied patterns from** (never import) are
`sop-hub/backend/app/core/limiter.py` (Supabase JWKS verify),
`.../services/audit.py` (hash chain), `.../core/capabilities.py` (registry).

## Topology — two services, one database

```
 operators ──SSO/MFA──▶ Admin SPA (admin/)  ──operator JWT──▶ Admin API (backend/)
                                                                │ service-role key
 tenants ───────────▶ ForgeSOP app (sop-hub, SEPARATE repo) ──┐ │
                                                              ▼ ▼
                                              Supabase Postgres (ONE shared DB)
                                              ├── public.*    tenant data (RLS) ── read by us
                                              └── platform.*  operator data ────── OWNED by us
```

- This service owns a dedicated **`platform` Postgres schema** in the **shared**
  Supabase database, versioned by **this repo's own Alembic migrations**
  (`backend/alembic/`).
- It connects as the **SERVICE-ROLE** Postgres role (RLS-bypassing), with
  `search_path = platform, public` pinned in `backend/app/core/db.py`.
- It **reads `public.*`** (workspaces, users, audit_trail, module tables) and
  **writes only `public.workspaces.feature_*`** (via `apply_plan`, Phase 2).
  It **never inserts into a tenant table.**

## The TWO — and only two — sop-hub touch-points (in the OTHER repo)

Across this entire track, the sop-hub app is modified in exactly two bounded
places. **Neither lives in this repo** — they are sop-hub Supabase migrations /
code:

1. **Phase 4:** one additive column `public.workspaces.status`
   (`'ACTIVE'|'SUSPENDED'`) + a `status='SUSPENDED' → 403` check in sop-hub's
   tenant auth dependency (suspend-at-login). One sop-hub Supabase migration.
2. **Phase 5 (deferred):** an optional telemetry shim in sop-hub (metrics
   middleware → shared Redis + a 429 recorder + a real `/ready` probe) for the
   deferred API-health feature.

Everything else — operator API, plan logic, footprints, the SPA, all background
jobs — lives **here**.

## Hard guardrails (do not violate)

- **Alembic touches `platform` ONLY.** It must never emit DDL against `public`.
  Enforced by `version_table_schema='platform'` + the `include_object` filter in
  `backend/alembic/env.py`. Do **not** add `platform.*` tables to sop-hub's
  `migrations/` tree, and do **not** add `public.*` tables here.
- **Do NOT expose `platform` via Supabase.** Keep it out of Supabase's
  exposed-schema list so the anon/auth PostgREST API can't reach operator tables.
- **403, never 404, for non-operators.** This is a single operator surface, not
  a tenant feature flag — we don't 404-to-hide. `require_platform_admin` returns
  403 for an absent/invalid token, a failed issuer/audience check, or a
  missing/inactive operator row.
- **Distinct issuer + audience.** `verify_platform_token` REQUIRES the configured
  operator `PLATFORM_JWT_ISSUER` + `PLATFORM_JWT_AUDIENCE`, so a tenant Supabase
  JWT can never authenticate here. Operator IdP uses asymmetric signing
  (RS256/ES256); HS256 is rejected outright.
- **The service-role key lives only in this service's environment** — never in
  any browser, never logged.
- **Capabilities, not roles.** New gated actions are entries in
  `backend/app/core/platform_capabilities.py` + a
  `require_platform_capability("<key>")` at the route. Do **not** import sop-hub's
  `user_can` — it is workspace-scoped and meaningless for an operator with no
  `workspace_id`.
- **Audit everything.** Every mutation + sensitive read →
  `platform.platform_audit` via the canonical-JSON hash chain in
  `backend/app/services/platform_audit.py`. Never write to the tenant
  `public.audit_trail`.

## Operator identity & roles

Operators are rows in `platform.platform_admins` (keyed by email), **not** in
`public.users`, and have no `workspace_id`. Three platform roles, each mapped to
capabilities in `platform_capabilities.py`:

- **PLATFORM_SUPPORT** — read everything; impersonate with consent; no writes.
- **PLATFORM_OPS** — support + change plans, toggle flags, suspend/reactivate.
- **PLATFORM_ADMIN** — ops + manage `platform_admins` and platform settings.

## Audit chain

`platform_audit` is a single hash chain (SHA-256 over canonical JSON +
`previous_hash`). Linkage is by `previous_hash` **pointer**, not timestamp
ordering: the tip is the row whose `hash` no other row references.
`verify_chain()` walks from `'GENESIS'` and recomputes every row; because
`previous_hash` is itself a hashed input, tampering with any field or the link
is caught by one recompute. `record_platform_event(...)` must run inside the
caller's `AsyncSession` so the audit row commits with the action it documents.

## Layout

```
backend/
  app/
    core/        config, db (service-role engine, search_path=platform,public),
                 platform_auth (verify_platform_token, require_platform_admin,
                 require_platform_capability), platform_capabilities
    services/    platform_audit (record_platform_event, verify_chain),
                 tenant_directory (ONLY home for cross-tenant public.* SELECTs),
                 plan_service (ONLY writer of public.workspaces.feature_*),
                 plan_seeds (free/pro/enterprise — shared by migration + tests)
    api/v1/      me, admins, overview, signups, workspaces, users, plans
                 (future: footprints, write actions)
    models/      tables (Core tables for the `platform` schema; portable types),
                 public_tables (READ-ONLY mirrors of public.* — never migrated here)
    main.py      FastAPI app
  alembic/       platform-schema migrations ONLY (env.py guards against public)
  tests/         pytest (conftest runs the app on in-memory SQLite w/ ATTACH-ed
                 `platform`; RS256 tokens via a throwaway keypair)
admin/           Vite + React 19 + TS + Tailwind operator SPA (English-only)
```

## Local development

- **Backend:** `cd backend && python3 -m venv venv && ./venv/bin/pip install -r
  requirements.txt`; run `./venv/bin/uvicorn app.main:app --reload --port 8000`.
- **Migrations (prod / real Postgres):** `cd backend && ./venv/bin/alembic
  upgrade head` (reads `DATABASE_URL`; creates `platform` schema + tables).
- **Tests:** `cd backend && ./venv/bin/python -m pytest`. The suite needs **no**
  external Postgres — it runs the real app code on in-memory SQLite with an
  ATTACH-ed `platform` schema; the Postgres `uuid`/`jsonb`/`inet` types degrade
  via `.with_variant` in `models/tables.py`.
- **Frontend:** `cd admin && npm install && npm run dev` (port 5173; dev proxy
  forwards `/v1` to the backend on :8000).
- **Env:** copy `.env.example` → `backend/.env` and `admin/.env.example` →
  `admin/.env.local`.

## Phased build plan (this repo unless a phase says "sop-hub")

- **Phase 0 — Standalone service + security foundation.** ✅ scaffolded:
  `platform_admins` + `platform_audit` (Alembic), `platform_auth`,
  `platform_capabilities`, `platform_audit` service, `GET /v1/me`,
  `GET/POST/PATCH /v1/admins`, SPA shell + SSO login.
- **Phase 1 — Read-only cockpit.** ✅ shipped: `services/tenant_directory.py`
  (the ONLY home for cross-tenant `public.*` SELECTs); `GET /v1/overview`,
  `/v1/signups?range=`, `/v1/workspaces`, `/v1/workspaces/{id}`, `/v1/users`
  (all behind `require_platform_admin`); SPA Overview/Signups/Workspaces/Users
  pages. **No Alembic / no new tables** — read-only.
- **Phase 2 — Plans.** ✅ shipped: migration `0002_plans` (`platform.plans` +
  `platform.workspace_plans`, seeded free/pro/enterprise from
  `services/plan_seeds.py`, backfills every workspace onto `free`);
  `services/plan_service.py` — `apply_plan` (upsert assignment + reconcile
  `public.workspaces.feature_*` from `plans.feature_flags` in one audited
  `plan.changed` transaction) and `set_overrides` (one-off flag/limit grant,
  `plan.override`, plan_key unchanged); `GET/POST/PATCH /v1/plans` (gated
  `plans.manage`) + `PATCH /v1/workspaces/{id}` (gated `workspace.manage`); SPA
  Plans catalog + workspace plan selector / override switches with a confirm
  modal. **`public.workspaces.feature_*` is the ONLY write to a `public` table,
  and it goes exclusively through `plan_service`.** Reconciliation writes only
  the columns a plan/override lists (validated against the live columns);
  `stripe_*` stays NULL.
- **Phase 3 — Footprints.** `customer_footprint_daily` + `signup_events`;
  admin-side Celery + Redis rollups; sortable directory.
- **Phase 4 — Write actions.** `impersonation_sessions`; suspend/reactivate,
  user manage, impersonation (Supabase Admin API). **+ the sop-hub
  `workspaces.status` column + login check (touch-point #1).**
- **Phase 5 — API health & over-request (deferred).** `api_request_metrics`,
  `rate_limit_events`, dashboards. **+ the sop-hub telemetry shim
  (touch-point #2).**
- **Phase 6 — Billing-ready (optional).** Stripe webhook → `apply_plan`.
- **Phase 7 — Alerts & digests.** `platform_settings`; threshold alerts + digest.

## Testing conventions

Admin-side pytest must keep proving the security invariants: a valid **tenant**
token → 403 at `/v1/me`; an active operator → 200; inactive/unknown → 403; the
`platform_audit` chain verifies (and tampering breaks it). See
`backend/tests/test_auth.py`. New gated endpoints add RBAC-negative tests
(operator lacking the capability → 403). Phase 1's `backend/tests/test_read.py`
proves every read endpoint 403s without an operator token and that the seeded
tenants/users/signups read back correctly. The test harness ATTACHes a `public`
schema in SQLite (alongside `platform`) so cross-tenant reads are exercised with
no external Postgres. SPA tests run under `vitest` (`cd admin && npm test`).
