"""
Test harness for the admin service.

The same app code runs against an in-memory SQLite DB. The `platform` schema
is provided by ATTACH-ing an in-memory database AS platform (SQLite has no
native schemas), and the portable column types in `models/tables.py` degrade
the Postgres `uuid`/`jsonb`/`inet` types to generic ones.

Operator tokens are RS256, signed with a throwaway keypair generated here;
`_platform_signing_key` is monkeypatched to return the matching public key so
there is no network JWKS fetch. The issuer/audience gate is exercised for
real — a token minted with tenant-style iss/aud is rejected by `jwt.decode`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings

# ── Operator IdP test config (distinct from any tenant project) ──────────
PLATFORM_ISSUER = "https://operators.forgesop.test/"
PLATFORM_AUDIENCE = "forgesop-admin-console"

# A tenant Supabase JWT would carry these — deliberately different.
TENANT_ISSUER = "https://abcxyz.supabase.co/auth/v1"
TENANT_AUDIENCE = "authenticated"

# Throwaway RSA keypair for signing test tokens.
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def make_token(
    *,
    email: str,
    issuer: str = PLATFORM_ISSUER,
    audience: str = PLATFORM_AUDIENCE,
    expired: bool = False,
) -> str:
    now = datetime.now(tz=timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=15)
    payload = {
        "sub": str(uuid.uuid4()),
        "email": email,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, _PRIVATE_PEM, algorithm="RS256")


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch):
    """Point the gate at the test operator IdP and stub the signing key."""
    monkeypatch.setattr(app_settings, "PLATFORM_JWT_ISSUER", PLATFORM_ISSUER)
    monkeypatch.setattr(app_settings, "PLATFORM_JWT_AUDIENCE", PLATFORM_AUDIENCE)
    monkeypatch.setattr(app_settings, "PLATFORM_JWKS_URL", "https://stub/jwks.json")
    # Default to the IdP path so a local .env (PLATFORM_LOCAL_AUTH=true) never
    # bleeds into the suite; test_local_auth flips this on explicitly.
    monkeypatch.setattr(app_settings, "PLATFORM_LOCAL_AUTH", False)

    import app.core.platform_auth as platform_auth

    monkeypatch.setattr(
        platform_auth, "_platform_signing_key", lambda token: _PUBLIC_KEY
    )
    yield


@pytest_asyncio.fixture
async def db_session():
    """In-memory SQLite with ATTACH-ed `platform` + `public` schemas.

    `platform` holds this service's own tables (Phase 0); `public` holds
    read-only mirrors of the tenant tables we read across tenants (Phase 1).
    On real Postgres both schemas live in the shared Supabase DB and `public`
    is owned by sop-hub — here we ATTACH it so fixtures can seed tenant data.
    """
    from app.models.public_tables import public_metadata
    from app.models.tables import metadata

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _attach(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("ATTACH DATABASE ':memory:' AS platform")
        cur.execute("ATTACH DATABASE ':memory:' AS public")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.run_sync(_create_public_tables)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


def _create_public_tables(sync_conn):
    """Create the public mirrors plus the extra `feature_*` columns SELECT *
    reads. We add two flag columns so the dynamic feature-flag read has
    something to surface in tests."""
    from app.models.public_tables import public_metadata

    public_metadata.create_all(sync_conn)
    # feature_ehs_module + feature_risk_module are governed by the pro plan;
    # feature_phase_a is NOT listed by free/pro (enterprise-only) — it exists so
    # tests can prove reconciliation only touches the columns a plan lists.
    for col in ("feature_ehs_module", "feature_risk_module", "feature_phase_a"):
        sync_conn.exec_driver_sql(
            f"ALTER TABLE public.workspaces ADD COLUMN {col} BOOLEAN DEFAULT 0"
        )


@pytest_asyncio.fixture
async def seed_admins(db_session):
    """Insert one active and one inactive operator."""
    from app.models.tables import platform_admins

    now = datetime.now(tz=timezone.utc)
    active_id = str(uuid.uuid4())
    inactive_id = str(uuid.uuid4())
    support_id = str(uuid.uuid4())
    await db_session.execute(
        insert(platform_admins),
        [
            {
                "id": active_id,
                "email": "ops@forgesop.test",
                "role": "PLATFORM_OPS",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": inactive_id,
                "email": "former@forgesop.test",
                "role": "PLATFORM_ADMIN",
                "is_active": False,
                "created_at": now,
                "updated_at": now,
            },
            {
                # read-only operator — lacks plans.manage / workspace.manage.
                "id": support_id,
                "email": "support@forgesop.test",
                "role": "PLATFORM_SUPPORT",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    await db_session.commit()
    return {
        "active_id": active_id,
        "inactive_id": inactive_id,
        "support_id": support_id,
    }


@pytest_asyncio.fixture
async def seed_plans(db_session):
    """Insert the free/pro/enterprise catalog from the shared seed source."""
    from app.models.tables import plans
    from app.services.plan_seeds import PLAN_SEEDS

    now = datetime.now(tz=timezone.utc)
    await db_session.execute(
        plans.insert(),
        [
            {
                "id": str(uuid.uuid4()),
                "key": p["key"],
                "name": p["name"],
                "description": p["description"],
                "feature_flags": p["feature_flags"],
                "limits": p["limits"],
                "is_public": p["is_public"],
                "sort_order": p["sort_order"],
                "monthly_price_cents": p["monthly_price_cents"],
                "created_at": now,
                "updated_at": now,
            }
            for p in PLAN_SEEDS
        ],
    )
    await db_session.commit()
    return {p["key"]: p for p in PLAN_SEEDS}


# Days-ago offsets for the four seeded users (see seed_tenants). Chosen to sit
# clearly inside/outside the 24h / 7d / 30d windows so KPI asserts are exact.
USER_OFFSETS_DAYS = {
    "u1": 0,   # ~now (2h ago): in 24h, 7d, 30d
    "u2": 3,   # in 7d, 30d
    "u3": 15,  # in 30d only
    "u4": 40,  # outside all windows
}


@pytest_asyncio.fixture
async def seed_tenants(db_session):
    """Seed two workspaces, four users, and audit rows on a known timeline."""
    from app.models.public_tables import audit_trail, users, workspaces

    now = datetime.now(tz=timezone.utc)
    ws_a = str(uuid.uuid4())  # active
    ws_b = str(uuid.uuid4())  # suspended, created outside the 30d window

    await db_session.execute(
        workspaces.insert(),
        [
            {
                "id": ws_a,
                "name": "Acme Corp",
                "slug": "acme",
                "is_suspended": False,
                "created_at": now - timedelta(days=2),
            },
            {
                "id": ws_b,
                "name": "Globex",
                "slug": "globex",
                "is_suspended": True,
                "created_at": now - timedelta(days=40),
            },
        ],
    )
    # feature_* columns aren't on the Core mirror — set them with raw SQL so the
    # dynamic SELECT * feature-flag read has something to surface. feature_phase_a
    # starts ON so Phase-2 tests can prove applying pro leaves it untouched.
    await db_session.execute(
        text(
            "UPDATE public.workspaces "
            "SET feature_ehs_module = 1, feature_phase_a = 1 WHERE id = :id"
        ),
        {"id": ws_a},
    )

    u1, u2, u3, u4 = (str(uuid.uuid4()) for _ in range(4))
    await db_session.execute(
        users.insert(),
        [
            {
                "id": u1, "email": "alice@acme.test", "first_name": "Alice",
                "last_name": "Adams", "role": "ADMIN", "status": "ACTIVE",
                "workspace_id": ws_a, "login_count": 12,
                "last_active_at": now - timedelta(hours=1),
                "created_at": now - timedelta(hours=2),
            },
            {
                "id": u2, "email": "bob@acme.test", "first_name": "Bob",
                "last_name": "Brown", "role": "MEMBER", "status": "ACTIVE",
                "workspace_id": ws_a, "login_count": 3,
                "last_active_at": now - timedelta(days=2),
                "created_at": now - timedelta(days=3),
            },
            {
                "id": u3, "email": "carol@acme.test", "first_name": "Carol",
                "last_name": "Clark", "role": "MEMBER", "status": "PENDING",
                "workspace_id": ws_a, "login_count": 0, "last_active_at": None,
                "created_at": now - timedelta(days=15),
            },
            {
                "id": u4, "email": "dave@globex.test", "first_name": "Dave",
                "last_name": "Davis", "role": "ADMIN", "status": "ACTIVE",
                "workspace_id": ws_b, "login_count": 99,
                "last_active_at": now - timedelta(days=39),
                "created_at": now - timedelta(days=40),
            },
        ],
    )

    await db_session.execute(
        audit_trail.insert(),
        [
            {
                "audit_id": str(uuid.uuid4()),
                "timestamp": now - timedelta(hours=1),
                "event_type": "document.published", "action": "publish",
                "actor_id": u1, "actor_email": "alice@acme.test",
                "actor_name": "Alice Adams", "organization_id": ws_a,
            },
            {
                "audit_id": str(uuid.uuid4()),
                "timestamp": now - timedelta(hours=3),
                "event_type": "workflow.approved", "action": "approve",
                "actor_id": u2, "actor_email": "bob@acme.test",
                "actor_name": "Bob Brown", "organization_id": ws_a,
            },
        ],
    )

    await db_session.commit()
    return {
        "ws_a": ws_a,
        "ws_b": ws_b,
        "users": {"u1": u1, "u2": u2, "u3": u3, "u4": u4},
        "total_users": 4,
        "total_workspaces": 2,
        "active_workspaces": 1,
    }


@pytest_asyncio.fixture
async def client(db_session):
    """AsyncClient bound to the app, with get_db overridden to the test session."""
    from app.core.db import get_db
    from app.main import app

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
