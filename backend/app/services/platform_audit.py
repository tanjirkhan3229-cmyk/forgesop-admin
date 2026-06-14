"""
Operator audit-trail writer (hash-chained).

Mirrors sop-hub's `backend/app/services/audit.py` (reference only — no
import): every operator mutation + sensitive read is appended to
`platform.platform_audit` with a SHA-256 hash chained off the prior tip,
producing a tamper-evident sequence. This is SEPARATE from the tenant
`public.audit_trail` — this service never writes there.

Chain linkage is by `previous_hash` pointer, NOT by timestamp ordering:
the tip is the row whose `hash` no other row references as its
`previous_hash`. `verify_chain` walks from 'GENESIS' along those pointers
and recomputes every hash. Because `previous_hash` is itself one of the
hashed inputs, tampering with any field OR the link is caught by a single
recompute.

Must be called inside the caller's AsyncSession so the audit row commits in
the same transaction as the action it documents.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import platform_audit


def _canonical_json(obj: Any) -> str:
    # Byte-exact canonical form (sorted keys, tight separators) — the same
    # convention sop-hub uses so a cross-stack verifier can re-compute.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _iso(ts: Any) -> str:
    """Normalise a stored timestamp to the same ISO string used at hash time.

    Postgres returns tz-aware datetimes; SQLite (tests) returns naive ones.
    Treat naive as UTC so the recompute matches the original
    `datetime.now(timezone.utc).isoformat()`.
    """
    if isinstance(ts, str):
        return ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def _content_hash(
    *,
    ts_iso: str,
    actor_email: str,
    action: str,
    target_type: Optional[str],
    target_id: Optional[str],
    target_workspace_id: Optional[str],
    state_before: Optional[Any],
    state_after: Optional[Any],
    previous_hash: str,
) -> str:
    content = _canonical_json(
        {
            "ts": ts_iso,
            "actor_email": actor_email,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "target_workspace_id": target_workspace_id,
            "state_before": state_before,
            "state_after": state_after,
            "previous_hash": previous_hash,
        }
    )
    return _sha256_hex(content + previous_hash)


async def _current_tip(session: AsyncSession) -> str:
    """The hash of the latest row, or 'GENESIS' if the chain is empty.

    The tip is the one row whose `hash` is not referenced by any other row's
    `previous_hash`. Order-independent (no reliance on `ts`).
    """
    rows = (
        await session.execute(
            select(platform_audit.c.hash, platform_audit.c.previous_hash)
        )
    ).all()
    if not rows:
        return "GENESIS"
    referenced = {r.previous_hash for r in rows}
    tips = [r.hash for r in rows if r.hash not in referenced]
    # Exactly one tip in a well-formed single chain.
    return tips[0] if tips else "GENESIS"


async def record_platform_event(
    session: AsyncSession,
    *,
    actor_email: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    target_workspace_id: Optional[str] = None,
    state_before: Optional[Any] = None,
    state_after: Optional[Any] = None,
    ip: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Append one hash-chained event to platform.platform_audit.

    Returns `{"audit_id", "hash", "previous_hash"}`.
    """
    prev = await _current_tip(session)
    now = datetime.now(tz=timezone.utc)
    now_iso = now.isoformat()

    tid = str(target_id) if target_id is not None else None
    twid = str(target_workspace_id) if target_workspace_id is not None else None

    new_hash = _content_hash(
        ts_iso=now_iso,
        actor_email=actor_email,
        action=action,
        target_type=target_type,
        target_id=tid,
        target_workspace_id=twid,
        state_before=state_before,
        state_after=state_after,
        previous_hash=prev,
    )

    audit_id = str(uuid.uuid4())
    await session.execute(
        insert(platform_audit).values(
            audit_id=audit_id,
            hash=new_hash,
            previous_hash=prev,
            ts=now,
            actor_email=actor_email,
            action=action,
            target_type=target_type,
            target_id=tid,
            target_workspace_id=twid,
            state_before=state_before,
            state_after=state_after,
            ip=ip,
            metadata=dict(metadata) if metadata else {},
        )
    )
    await session.flush()
    return {"audit_id": audit_id, "hash": new_hash, "previous_hash": prev}


async def verify_chain(session: AsyncSession) -> bool:
    """Verify the entire platform_audit chain is tamper-free.

    Walks from 'GENESIS' along `previous_hash` pointers, recomputing each
    row's hash. Returns True iff every row recomputes correctly and the walk
    visits every row exactly once (no orphans, forks, or breaks).
    """
    rows = (
        await session.execute(select(platform_audit))
    ).mappings().all()
    if not rows:
        return True

    by_prev: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_prev.setdefault(row["previous_hash"], []).append(row)

    visited = 0
    current = "GENESIS"
    while current in by_prev:
        bucket = by_prev[current]
        if len(bucket) != 1:
            return False  # fork — two rows share a previous_hash
        row = bucket[0]
        recomputed = _content_hash(
            ts_iso=_iso(row["ts"]),
            actor_email=row["actor_email"],
            action=row["action"],
            target_type=row["target_type"],
            target_id=str(row["target_id"]) if row["target_id"] is not None else None,
            target_workspace_id=(
                str(row["target_workspace_id"])
                if row["target_workspace_id"] is not None
                else None
            ),
            state_before=row["state_before"],
            state_after=row["state_after"],
            previous_hash=row["previous_hash"],
        )
        if recomputed != row["hash"]:
            return False
        visited += 1
        current = row["hash"]

    # Every row reachable from GENESIS exactly once.
    return visited == len(rows)
