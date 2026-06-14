# ForgeSOP Platform Admin Console

A **standalone, cross-tenant operator console** for ForgeSOP staff — signups,
customer footprints, user / workspace / plan management, and (deferred) API
health monitoring.

> **This is not the ForgeSOP product.** It is a separate service on its own
> origin, behind SSO + MFA, that **shares** the ForgeSOP Supabase database and
> owns a dedicated `platform` schema. It bypasses RLS (reads every tenant) and
> must never be reachable by a tenant. See [CLAUDE.md](./CLAUDE.md) for the full
> architecture and guardrails.

## Quick start

```bash
# backend
cd backend
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp ../.env.example .env            # fill in DATABASE_URL (service-role) + IdP
./venv/bin/python -m pytest        # green, no external Postgres needed
./venv/bin/uvicorn app.main:app --reload --port 8000

# migrations against a real Postgres (creates the `platform` schema + tables)
./venv/bin/alembic upgrade head

# frontend
cd ../admin
npm install
cp .env.example .env.local
npm run dev                        # http://localhost:5173
```

## Phase 0 (this scaffold)

- `platform.platform_admins` + `platform.platform_audit` (Alembic, `platform`
  schema only).
- Operator auth gate (`require_platform_admin`) — distinct issuer/audience so a
  tenant JWT is rejected; **403, never 404**, for non-operators.
- Hash-chained `platform_audit` (`record_platform_event` / `verify_chain`).
- `GET /v1/me`, `GET/POST/PATCH /v1/admins`.
- React 19 + Vite + Tailwind SPA shell with SSO login that renders `/v1/me`.

See [CLAUDE.md](./CLAUDE.md) for the full phase list and the two (and only two)
sop-hub touch-points.
