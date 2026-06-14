"""
Operator authentication + authorization gate.

This is the load-bearing security boundary of the console (Architecture §4).
A cross-tenant operator surface is a high-value target; treat it like
production infrastructure.

Token model (distinct from tenants):
  * Operators authenticate via the operator IdP (SSO/OIDC + MFA), which mints
    short-lived tokens with a DISTINCT issuer + audience from the tenant
    Supabase project.
  * `verify_platform_token` verifies the signature against PLATFORM_JWKS_URL
    and REQUIRES the configured issuer + audience. A tenant Supabase JWT
    therefore can never satisfy this gate even if it is otherwise valid.

Authorization:
  * `require_platform_admin` decodes the token, looks up an ACTIVE
    `platform.platform_admins` row by email, and attaches a `PlatformActor`.
    A non-operator (or inactive/missing row) gets **403** — this is a single
    operator surface, not a tenant feature flag, so we do NOT 404 to hide it.
  * `require_platform_capability(cap)` gates mutations on the operator's
    platform role via the capability registry.

The JWKS verification approach is COPIED from sop-hub's
`backend/app/core/limiter.py` (reference only — no sop-hub import).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.platform_capabilities import role_has_capability
from app.models.tables import platform_admins

logger = logging.getLogger(__name__)

# Operator IdP keys are asymmetric (RS256 / ES256). PyJWKClient caches the
# JWKS internally so we don't refetch per request.
_jwks_client_singleton: Optional[PyJWKClient] = None


def _platform_jwks_client() -> PyJWKClient:
    global _jwks_client_singleton
    if _jwks_client_singleton is None:
        if not settings.PLATFORM_JWKS_URL:
            raise InvalidTokenError("PLATFORM_JWKS_URL is not configured.")
        _jwks_client_singleton = PyJWKClient(
            settings.PLATFORM_JWKS_URL, cache_keys=True
        )
    return _jwks_client_singleton


def _platform_signing_key(token: str):
    """Resolve the signing key for `token` from the operator JWKS.

    Isolated so tests can monkeypatch it with a local public key without a
    network round-trip.
    """
    return _platform_jwks_client().get_signing_key_from_jwt(token).key


def verify_platform_token(token: str) -> dict:
    """Verify an operator token and return its claims.

    Raises `InvalidTokenError` (or a subclass) on any failure: bad signature,
    wrong issuer, wrong audience, expiry. The issuer + audience checks are
    what make a tenant Supabase JWT unusable here.
    """
    if not settings.PLATFORM_JWT_ISSUER or not settings.PLATFORM_JWT_AUDIENCE:
        raise InvalidTokenError(
            "PLATFORM_JWT_ISSUER and PLATFORM_JWT_AUDIENCE must be configured."
        )

    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "RS256")
    if alg not in ("RS256", "ES256"):
        # Operator IdP must use asymmetric signing. Reject HS256 outright so a
        # leaked shared secret can never mint an operator token.
        raise InvalidTokenError(f"Unsupported operator token alg: {alg!r}")

    signing_key = _platform_signing_key(token)
    return jwt.decode(
        token,
        signing_key,
        algorithms=[alg],
        issuer=settings.PLATFORM_JWT_ISSUER,
        audience=settings.PLATFORM_JWT_AUDIENCE,
        options={"require": ["exp", "iss", "aud"]},
    )


@dataclass(frozen=True)
class PlatformActor:
    """An authenticated, active operator."""

    id: str
    email: str
    role: str

    def has_capability(self, capability: str) -> bool:
        return role_has_capability(self.role, capability)


_bearer_scheme = HTTPBearer(auto_error=False)

_FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Not a platform operator",
)


async def require_platform_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> PlatformActor:
    """Gate every operator route.

    403 (never 404 / 401-leak) when the token is absent, invalid, fails the
    issuer/audience check (e.g. a tenant JWT), or maps to no active operator.
    """
    if not credentials:
        raise _FORBIDDEN

    try:
        claims = verify_platform_token(credentials.credentials)
    except InvalidTokenError as exc:
        logger.warning("platform token rejected: %s", exc)
        raise _FORBIDDEN

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise _FORBIDDEN

    row = (
        await db.execute(
            select(
                platform_admins.c.id,
                platform_admins.c.email,
                platform_admins.c.role,
                platform_admins.c.is_active,
            ).where(platform_admins.c.email == email)
        )
    ).mappings().first()

    if row is None or not row["is_active"]:
        raise _FORBIDDEN

    return PlatformActor(
        id=str(row["id"]),
        email=row["email"],
        role=row["role"],
    )


def require_platform_capability(capability: str):
    """Dependency factory: require `actor` to hold `capability`.

    Layer on top of `require_platform_admin` at mutation routes, e.g.
    `Depends(require_platform_capability("workspace.suspend"))`.
    """

    async def _dep(
        actor: PlatformActor = Depends(require_platform_admin),
    ) -> PlatformActor:
        if not actor.has_capability(capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operator role {actor.role} lacks capability {capability}",
            )
        return actor

    return _dep
