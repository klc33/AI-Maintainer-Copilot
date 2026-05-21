# app/repositories/widget_configs.py
"""asyncpg-backed CRUD for the widget_configs table.

Uses the existing async session pool when available; falls back to a
dedicated asyncpg pool for the JSONB/text[] columns since SQLAlchemy 2's
text[] handling is more awkward than asyncpg's native support."""
from __future__ import annotations

import os
import json
import asyncio
import asyncpg


_RAW_DB_URL = os.environ["DATABASE_URL"]
_DB_URL = _RAW_DB_URL.replace("+asyncpg", "")

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=5)
    return _pool


def _row_to_dict(row) -> dict:
    """asyncpg returns Record; JSONB comes back as a str — decode it here so
    callers always get a plain dict for `theme`."""
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("theme"), str):
        d["theme"] = json.loads(d["theme"])
    return d


async def get(widget_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM widget_configs WHERE widget_id = $1",
            widget_id,
        )
    return _row_to_dict(row)


async def list_all() -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM widget_configs ORDER BY created_at DESC"
        )
    return [_row_to_dict(r) for r in rows]


async def list_active() -> list[dict]:
    """Public-facing listing: active widgets only. Used by the demo host
    chooser. Returns the same fields as list_all() — callers project down to
    what they want to expose."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM widget_configs WHERE is_active = true ORDER BY name, widget_id"
        )
    return [_row_to_dict(r) for r in rows]


async def create(
    widget_id: str,
    name: str,
    allowed_origins: list[str],
    theme: dict,
    enabled_tools: list[str],
    is_active: bool = True,
    description: str = "",
) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO widget_configs (widget_id, name, description, allowed_origins, theme, enabled_tools, is_active)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            RETURNING *
            """,
            widget_id, name, description, allowed_origins, json.dumps(theme), enabled_tools, is_active,
        )
    return _row_to_dict(row)


async def update(
    widget_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    allowed_origins: list[str] | None = None,
    theme: dict | None = None,
    enabled_tools: list[str] | None = None,
    is_active: bool | None = None,
) -> dict | None:
    """Partial update. Only fields explicitly passed are touched."""
    sets, params = [], []

    def add(col, val, cast=""):
        params.append(val)
        sets.append(f"{col} = ${len(params)}{cast}")

    if name is not None:               add("name", name)
    if description is not None:        add("description", description)
    if allowed_origins is not None:    add("allowed_origins", allowed_origins)
    if theme is not None:              add("theme", json.dumps(theme), "::jsonb")
    if enabled_tools is not None:      add("enabled_tools", enabled_tools)
    if is_active is not None:          add("is_active", is_active)

    if not sets:
        return await get(widget_id)

    sets.append("updated_at = now()")
    params.append(widget_id)
    sql = f"UPDATE widget_configs SET {', '.join(sets)} WHERE widget_id = ${len(params)} RETURNING *"

    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)
    return _row_to_dict(row)


async def delete(widget_id: str) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM widget_configs WHERE widget_id = $1",
            widget_id,
        )
    # asyncpg returns 'DELETE 1' or 'DELETE 0'
    return result.endswith(" 1")
