# app/repositories/audit_log.py
"""Append-only audit log writes. Schema lives in migration 001.

Currently only memory writes are audited. Wider coverage (role changes,
widget config CRUD, conversation deletions — see open issues) would also
go through this module."""
from __future__ import annotations

import json

from app.db.pool import get_pool


async def record(
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    trace_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Insert one audit entry. Safe to call from any service; the table is
    append-only and never blocks the caller's main path for long."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_log (
                trace_id, actor_type, actor_id, action, target_type, target_id, details
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            trace_id, actor_type, actor_id, action, target_type, target_id,
            json.dumps(details) if details else None,
        )
