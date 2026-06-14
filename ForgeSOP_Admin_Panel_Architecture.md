# ForgeSOP Platform Admin Console — Architecture Plan

**Status:** Draft v0.2 · 2026-06-14 · owner Tanjir
**Scope:** A cross-tenant operator console for the ForgeSOP SaaS — signups,
customer footprints, user & workspace management, plan changes, and (deferred)
API health + over-request monitoring.
**Change in v0.2:** the console is a **standalone service** (its own backend +
frontend + deploy) on the **shared** ForgeSOP database, **not** a router inside
the existing FastAPI app. API-health telemetry is **deferred** to a later phase.

> This plans a **platform (operator) console**, not the per-workspace admin that
> already ships inside the product. That distinction (§1) is the most important
> idea here; the two-service topology (§3) is the second.

---

## 1. Tenant admin vs. platform admin (read this first)

ForgeSOP already has an in-app admin surface (**Settings → Permissions / Modules**,
the role hierarchy, the capability system). That is a **tenant admin**: a
workspace owner administering *their own* workspace. It is RLS-scoped, every query
is filtered to the caller's `workspace_id`, and it ships in the bundle every
customer downloads.

This project is a **platform / operator console**: *you*, the SaaS operator,
looking *across all tenants*. It must **bypass RLS** (it reads all workspaces) and
must **never be reachable by a tenant**. Different security model → different home.

| | Tenant admin (exists) | Platform console (this plan) |
|---|---|---|
| Audience | Workspace owners/admins | ForgeSOP staff/operators |
| Data scope | One workspace (RLS-enforced) | All workspaces (RLS-bypassed) |
| Ships to | Every customer's browser | Operators only, isolated service |
| Identity | `public.users` row + role | `platform.platform_admins` operator |
| Blast radius if buggy | One tenant | Every tenant |

---

## 2. Confirmed decisions

1. **Deployment:** a **standalone admin service** — its own backend, its own SPA,
   its own deploy (e.g. `admin.forgesop.app` + `admin-api.forgesop.app`) — that
   connects to the **same Supabase database** via the service-role key. It is
   **not** a router in the existing FastAPI app. *(§3)*
2. **Plans / billing:** add a **plan model + limits now, manage manually**, with
   nullable Stripe columns so billing drops in later. No payment integration in
   v1. *(§5)*
3. **API health / over-request:** **deferred.** Ship signups, footprints, plans,
   and user/workspace management first; add health + over-request telemetry as a
   later phase, because it is the only part that requires touching the main app. *(§8)*

---

## 3. Topology — two services, one database

```
 operators ──SSO/MFA──▶  Admin SPA (admin.forgesop.app)
                              │ operator JWT
                              ▼
                      Admin API service  ────────────┐  (FastAPI + Alembic + Celery)
                      owns the `platform` schema      │   service-role key
 tenants ───────────▶  ForgeSOP app  ───┐             │
                       (FastAPI + React) │            │
                                         ▼            ▼
                               Supabase Postgres (ONE database)
                               ├── public.*    (tenant data, RLS)   ◀── read by admin via service-role
                               └── platform.*  (operator data)      ◀── owned by the admin service
```

**The admin service is self-contained.** It runs its own FastAPI process, its own
Celery worker/beat (from Phase 3), its own Redis, and its own React/Vite SPA,
deployed independently. It owns a dedicated **`platform` Postgres schema** inside
the shared Supabase database and manages that schema with **its own migrations
(Alembic)** — entirely separate from sop-hub's Supabase migration tree, which
continues to own `public`.

**How it reaches tenant data.** The backend already connects to Supabase as the
**service role** (`SUPABASE_DB_ROLE`), which bypasses RLS. The admin service uses
the same service-role credential to **read `public.*`** (workspaces, users,
audit_trail, module tables) and to **write only the `feature_*` flag columns on
`public.workspaces`** when applying a plan. It never inserts into a tenant table.

**The main app (sop-hub) is touched in exactly two small, bounded places across
the entire track:**
1. **One additive column** — `public.workspaces.status` (`ACTIVE`/`SUSPENDED`) so
   the tenant auth path can block a suspended workspace at login (Phase 4). One-line
   Supabase migration in sop-hub.
2. **The deferred telemetry shim** — a metrics middleware + 429 recorder + `/ready`
   probe (Phase 5, only if/when API health is built). Until then, sop-hub is
   untouched.

Everything else — the operator API, plan logic, footprint rollups, the SPA, all
background jobs — lives in the standalone service.

**Isolation properties this buys:**
- A separate process + host is a hard boundary; a tenant token cannot reach the
  admin API even by mistake.
- The `platform` schema is **not** in Supabase's exposed-schemas list, so the
  tenant-facing PostgREST/anon API cannot see operator tables at all.
- The admin service can be locked behind SSO + MFA + an IP/VPN allowlist
  independently of the product.

---

## 4. Security & access model (the load-bearing part)

A cross-tenant console is a high-value target. Treat it like production
infrastructure.

### 4.1 Network & surface isolation
- Admin SPA and admin API each on their **own origin**, behind **Cloudflare
  Access / VPN / IP allowlist** so the login surface isn't on the open internet.
- The admin API holds the **service-role key**; that secret lives only in the
  admin service's environment, never in any browser.

### 4.2 Operator identity (separate from tenants)
- `platform.platform_admins` keyed by email, carrying a platform role
  (`PLATFORM_SUPPORT` / `PLATFORM_OPS` / `PLATFORM_ADMIN`). Operators are **not**
  rows in `public.users` and have no `workspace_id`.
- Authenticate via **SSO (Google Workspace / OIDC) with mandatory MFA**, minted
  into short-lived tokens with a **distinct issuer/audience** so a tenant Supabase
  JWT can never satisfy the admin gate. Reuse the JWKS verification approach from
  the main app's `core/limiter.py` as a reference implementation.

### 4.3 The gate
`require_platform_admin` validates the operator token, looks up the active
`platform_admins` row, and attaches a `PlatformActor`. Destructive actions add
`require_platform_capability("workspace.suspend", ...)`. The admin service has its
own capability registry (it cannot reuse the tenant `user_can`, which resolves
against a workspace + `user_capability_grants`).

### 4.4 Platform roles → capabilities (v1 starter set)
- **PLATFORM_SUPPORT** — read everything; impersonate with consent; no writes.
- **PLATFORM_OPS** — support + change plans, toggle flags, suspend/reactivate.
- **PLATFORM_ADMIN** — ops + manage `platform_admins` and platform settings.

### 4.5 Audit & accountability
- `platform.platform_audit` (hash-chained; mirror the canonical-JSON pattern in
  the main app's `services/audit.py`) records **every** mutation + sensitive read:
  actor, action, target workspace/user, `state_before`/`state_after`, IP. Separate
  from the tenant `public.audit_trail`.
- **Re-auth on destructive actions** (suspend, delete, plan change, impersonation).
- **RLS-bypass discipline:** the service-role bypasses RLS, so the admin service is
  the *only* thing standing between an operator and all-tenant data. All
  cross-tenant SQL lives in the admin service's `services/` layer and is reviewed
  as deliberate. (There are no tenant routers in this codebase to leak into — a
  benefit of the separate service.)

### 4.6 Impersonation / "view as tenant"
- Time-boxed, logged to `platform.impersonation_sessions` + `platform_audit`,
  banner-flagged, ideally ticket-linked.
- The admin service mints a scoped, short-lived **tenant** session via the
  **Supabase Admin API** (service-role) for the target user — it does not call the
  main backend and never exposes the service-role key to a browser.

### 4.7 PII handling
- Footprints/metrics are aggregates. Raw PII (emails, names, free-text) is gated to
  higher platform roles and redacted in exports.

---

## 5. Data model — the `platform` schema (admin-service-owned)

All operator tables live in a new **`platform`** schema, created and versioned by
the **admin service's own Alembic migrations** (the admin service's Alembic config
is scoped to `platform` and must **never** touch `public`). Tenant tables in
`public.*` keep being owned by sop-hub's Supabase migration tree.

| Table (`platform.*`) | Purpose | Phase |
|---|---|---|
| `platform_admins` | Operator allowlist + role + `is_active`. | 0 |
| `platform_audit` | Hash-chained log of operator actions. | 0 |
| `plans` | Plan catalog — `key`, `name`, `feature_flags jsonb`, `limits jsonb`, nullable `stripe_price_id`/`monthly_price_cents`. | 2 |
| `workspace_plans` | Plan assignment per workspace — `workspace_id`, `plan_key`, `plan_overrides jsonb`, `trial_ends_at`, nullable `stripe_customer_id`/`stripe_subscription_id`. | 2 |
| `customer_footprint_daily` | Daily per-workspace usage snapshot + engagement score. | 3 |
| `signup_events` | Funnel/source capture (basic counts still derive from `public.users.created_at`). | 3 |
| `impersonation_sessions` | Audit of impersonation. | 4 |
| `api_request_metrics` | Per-(route, status, workspace, bucket) rollups. **Deferred.** | 5 |
| `rate_limit_events` | One row per 429. **Deferred.** | 5 |
| `platform_settings` | Alert thresholds, digest recipients. | 7 |

**Plan assignment vs. effect.** "Which plan a workspace is on" lives in
`platform.workspace_plans` (operator-owned). The *effect* — feature flags the
tenant app reads — is applied by writing the existing `public.workspaces.feature_*`
columns. `apply_plan(workspace_id, plan_key)` does both in one transaction and
audits it. This is the billing-later seam: a future Stripe webhook just calls
`apply_plan`.

**The only `public` schema change in the data model** is `public.workspaces.status`
(Phase 4), added via a normal sop-hub Supabase migration so the tenant auth path
can read it. (Alternative considered: bulk-flip `public.users.status`, which
already exists and is already enforced at login — rejected because restore-after-
suspend is fiddly; a single workspace-level column is cleaner.)

---

## 6. Admin service — backend

A standalone **FastAPI** service (same stack as the product for consistency; it is
free to be anything since it only talks to Postgres + the Supabase Admin API).
Suggested layout in a new repo (`forgesop-admin/`):

```
backend/app/
  core/        config, platform_auth (verify_platform_token, require_platform_admin),
               platform_capabilities, db (service-role engine, search_path=platform,public)
  services/    tenant_directory (cross-tenant reads), plan_service, footprint_service,
               platform_audit, impersonation
  api/v1/      overview, signups, workspaces, users, plans, footprints, audit, admins
  alembic/     platform-schema migrations ONLY
  tasks/       celery app + footprint rollups (Phase 3)
```

Endpoint inventory (rooted on the admin host; all behind `require_platform_admin`):

| Area | Endpoint | Phase |
|---|---|---|
| Overview | `GET /v1/overview` | 1 |
| Signups | `GET /v1/signups?range=` | 1 |
| Workspaces | `GET /v1/workspaces` · `GET /v1/workspaces/{id}` | 1 |
| Users | `GET /v1/users` | 1 |
| Plans | `GET/POST/PATCH /v1/plans` · `PATCH /v1/workspaces/{id}` (apply plan / flags) | 2 |
| Footprints | `GET /v1/footprints` · `/{workspace_id}` | 3 |
| Write actions | `POST /v1/workspaces/{id}/suspend`·`/reactivate` · `PATCH /v1/users/{id}` · `POST /v1/users/{id}/impersonate` | 4 |
| Audit | `GET /v1/audit` | 4 |
| Health | `GET /v1/metrics/*` · `GET /v1/health` | 5 (deferred) |
| Operators | `GET/POST/PATCH /v1/admins` | 0 |

---

## 7. Customer footprints (definition)

A per-tenant usage & engagement profile, computed daily into
`platform.customer_footprint_daily` from `public.*` + (later) the metrics tables:

- **Engagement:** active users (distinct `public.audit_trail.actor_id` over 1/7/30d),
  `public.users.last_active_at`, `login_count`.
- **Adoption:** counts of SOPs, incidents, CAPAs, risks, inspections; flags on vs. used.
- **Volume:** storage; API calls (once Phase 5 lands); export counts.
- **Commercial:** current plan, seats used vs. `plans.limits.max_seats`, over-limit
  flags, trial status.
- **Health score:** weighted blend (recency + module breadth + seat utilization) to
  surface churn risk and expansion candidates.

Powers the per-workspace detail page and a sortable directory ("over seat limit",
"inactive 14+ days").

---

## 8. API health & over-request monitoring (DEFERRED — Phase 5)

Deferred because it is the only capability that requires changing the main app:
in-app `slowapi` rate limits and per-route latency are only visible **inside** the
ForgeSOP process. When built, the plan is a **thin telemetry shim in the main app**:

- A `MetricsMiddleware` capturing the **route template** (not raw URL), status, and
  latency into **shared Redis** counters; the admin service's Celery drains them
  into `platform.api_request_metrics`. **No Postgres write on the request hot path.**
- A wrapper around slowapi's default `RateLimitExceeded` handler that records each
  429 (route, key, workspace) → `platform.rate_limit_events`. Powers "which API is
  getting over-requested".
- A real `/ready` probe (DB/Redis/Celery/Supabase) alongside the existing bare
  `/health` liveness stub.

Until Phase 5, the admin console's "Health" page can show a minimal status (ping
the main app's existing `/health`) and otherwise hide the metrics panels.
*(Alternative, if you later prefer zero main-app code: ingest request/latency/429
status from your edge proxy/gateway — less granular about which in-app limit
tripped.)*

---

## 9. Frontend — the admin SPA

New `forgesop-admin/` frontend: Vite + React 19 + TypeScript + Tailwind (copy the
product's tokens), **TanStack Query** for data, **recharts** for charts,
**lucide-react** for icons. SSO/OIDC + MFA → short-lived operator token on every
request. **English-only** (skip the 9-locale i18n pipeline). Pages: Overview ·
Signups · Workspaces · Users · Plans · Footprints · Audit · Operators · (Health,
Phase 5). Destructive actions behind confirm + re-auth; persistent impersonation
banner.

---

## 10. Phased build plan

Each phase is an independently shippable PR (in the admin repo unless noted).

- **Phase 0 — Standalone service + security foundation.** Scaffold `forgesop-admin/`
  (FastAPI + Alembic scoped to `platform` + SPA shell). `platform_admins`,
  `platform_audit`, `platform_auth`, capability registry, SSO login, `GET /v1/me`.
- **Phase 1 — Read-only cockpit.** `tenant_directory` cross-tenant reads; overview,
  signups, workspaces, users; SPA pages. **No new tables.** *(Phases 0–1 alone
  deliver signup + customer visibility.)*
- **Phase 2 — Plans.** `plans` + `workspace_plans`; `apply_plan` reconciles
  `public.workspaces.feature_*`; plans UI + per-workspace switcher.
- **Phase 3 — Footprints.** `customer_footprint_daily` + `signup_events`; admin-side
  Celery beat rollups; footprints directory + filters. (Introduces the admin
  service's Redis + Celery.)
- **Phase 4 — Write actions.** `impersonation_sessions`; suspend/reactivate, user
  manage, impersonation (Supabase Admin API). **One sop-hub change:** add
  `public.workspaces.status` + a suspended-→403 check in the tenant auth path.
- **Phase 5 — API health & over-request (deferred).** The telemetry shim in sop-hub
  + `api_request_metrics` + `rate_limit_events` + admin dashboards.
- **Phase 6 — Billing-ready (optional).** Stripe webhook → `apply_plan`; invoices view.
- **Phase 7 — Alerts & digests.** `platform_settings`; threshold alerts + operator digest.

---

## 11. Cross-cutting concerns

- **Migration ownership is split and must stay split:** `public` → sop-hub Supabase
  migrations; `platform` → admin-service Alembic. Neither tool touches the other's
  schema. The single exception is the Phase-4 `public.workspaces.status` column,
  which is a sop-hub migration.
- **Do not expose `platform` via Supabase.** Keep it out of the API-exposed schema
  list so the anon/auth keys can't reach it.
- **Audit everything**; rate-limit the admin API per-operator; back up + TTL the
  metrics tables once Phase 5 lands.
- **Testing:** admin-side pytest (tenant token rejected / operator accepted,
  `platform_audit` chain, `apply_plan` reconciliation, RBAC negatives); the one
  sop-hub PR (Phase 4) adds a suspended-blocks-login test; vitest in the SPA.

---

## 12. Open decisions

1. **Operator IdP:** Google Workspace SSO via a dedicated OIDC client (distinct
   issuer/audience from the tenant Supabase project). *(Recommended.)*
2. **Admin service hosting:** same cloud/VPC as the DB (lowest latency for
   cross-tenant reads) vs. elsewhere. *(Recommend same VPC, private DB networking.)*
3. **Suspension semantics:** block at tenant login immediately (the
   `workspaces.status` check) vs. block writes only. *(Recommend block at login.)*
4. **Limit enforcement depth:** display-only vs. enforced (seats at invite). *(Recommend
   display + seat enforcement; in-app rate-limit-by-plan waits for Phase 5.)*

---

## 13. One-paragraph summary

Build the operator console as a **standalone service** — its own FastAPI backend,
React SPA, Celery, and Redis, deployed behind SSO/MFA on its own origin — that
connects to the **same Supabase database** via the service-role key. It owns a
dedicated **`platform` Postgres schema** (its own Alembic migrations), reads tenant
data from `public.*`, and writes back only the `feature_*` flags when applying a
plan. The **main app is touched in just two bounded places**: one
`workspaces.status` column for suspension, and a deferred telemetry shim if/when
API-health monitoring is built. Plans are a flag-bundle model now with nullable
Stripe seams for later; **API health and over-request monitoring are deferred** to
Phase 5 because they are the only parts that require changing the main app. Ship
read-only Phases 0–1 first for signup + customer visibility, then plans, footprints,
and write actions.
