"""
Local operator auth (self-contained email+password).

Active only when settings.PLATFORM_LOCAL_AUTH is True — an alternative to an
external IdP for small teams. Two concerns:

  * **Passwords** — hashed with PBKDF2-HMAC-SHA256 (stdlib, 600k iterations,
    per-hash random salt). No third-party crypto dependency. Stored in
    platform.platform_admins.password_hash; NULL = not set yet (first login).
  * **Sessions** — on successful login the console mints its OWN short-lived
    HS256 JWT signed with PLATFORM_SESSION_SECRET (a server-only secret that
    never leaves this service). require_platform_admin verifies it. HS256 is
    acceptable here precisely because the console is the sole issuer AND
    verifier — unlike the external-IdP path, which still rejects HS256 (a
    leaked tenant secret must never mint operator tokens).

This module never logs passwords or hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tables import platform_admins

# Console session token identity (self-issued; distinct from any tenant token).
SESSION_ISSUER = "forgesop-admin-console"
SESSION_AUDIENCE = "forgesop-admin-console"
SESSION_TYP = "session"

_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LEN = 12


# ── password hashing (PBKDF2-HMAC-SHA256, stdlib) ────────────────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _PBKDF2_ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# ── session tokens (HS256, server-only secret) ───────────────────────────────


def _session_secret() -> str:
    if not settings.PLATFORM_SESSION_SECRET:
        raise InvalidTokenError("PLATFORM_SESSION_SECRET is not configured")
    return settings.PLATFORM_SESSION_SECRET


def create_session_token(admin_id: str, email: str) -> str:
    now = int(time.time())
    ttl = int(settings.PLATFORM_SESSION_TTL_HOURS) * 3600
    payload = {
        "sub": str(admin_id),
        "email": email,
        "iss": SESSION_ISSUER,
        "aud": SESSION_AUDIENCE,
        "typ": SESSION_TYP,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _session_secret(), algorithm="HS256")


def verify_session_token(token: str) -> dict:
    """Verify a console session token. Raises InvalidTokenError on any failure."""
    claims = jwt.decode(
        token,
        _session_secret(),
        algorithms=["HS256"],  # console is sole issuer+verifier; secret is server-only
        issuer=SESSION_ISSUER,
        audience=SESSION_AUDIENCE,
        options={"require": ["exp", "iss", "aud"]},
    )
    if claims.get("typ") != SESSION_TYP:
        raise InvalidTokenError("not a session token")
    return claims


# ── DB-backed flows ──────────────────────────────────────────────────────────


async def _active_admin(session: AsyncSession, email: str):
    return (
        await session.execute(
            select(
                platform_admins.c.id,
                platform_admins.c.email,
                platform_admins.c.is_active,
                platform_admins.c.password_hash,
            ).where(platform_admins.c.email == email)
        )
    ).mappings().first()


async def authenticate(session: AsyncSession, email: str, password: str) -> dict:
    """Returns one of:
      {"status": "ok", "id", "email", "token"}    — valid credentials
      {"status": "password_set_required"}          — known admin, no password yet
      {"status": "invalid"}                        — unknown/inactive/bad password
    """
    email = (email or "").strip().lower()
    admin = await _active_admin(session, email)
    if admin is None or not admin["is_active"]:
        return {"status": "invalid"}
    if not admin["password_hash"]:
        return {"status": "password_set_required"}
    if not verify_password(password, admin["password_hash"]):
        return {"status": "invalid"}
    token = create_session_token(str(admin["id"]), admin["email"])
    return {"status": "ok", "id": str(admin["id"]), "email": admin["email"], "token": token}


async def set_initial_password(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    setup_token: Optional[str] = None,
) -> dict:
    """First-login password set. Allowed only for an active admin whose password
    is not yet set. Returns {"status": ...}. The caller commits + audits."""
    email = (email or "").strip().lower()

    # Optional defence against first-login takeover.
    if settings.PLATFORM_SETUP_TOKEN and setup_token != settings.PLATFORM_SETUP_TOKEN:
        return {"status": "setup_token_invalid"}

    admin = await _active_admin(session, email)
    if admin is None or not admin["is_active"]:
        return {"status": "invalid"}
    if admin["password_hash"]:
        return {"status": "already_set"}
    if len(password or "") < MIN_PASSWORD_LEN:
        return {"status": "weak"}

    await session.execute(
        update(platform_admins)
        .where(platform_admins.c.id == admin["id"])
        .values(password_hash=hash_password(password), password_set_at=_pg_now())
    )
    return {"status": "ok", "id": str(admin["id"]), "email": admin["email"]}


def _pg_now():
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc)
