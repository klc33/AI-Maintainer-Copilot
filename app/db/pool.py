# app/db/pool.py
"""One asyncpg pool shared by every repository.

Previously each repository (memories, widget_configs, …) carried its own
lazy pool, which meant we ran N independent connection pools against the
same database. One pool centralized here is enough; repos just call
`get_pool()` and use it.

The SQLAlchemy engine in app/db/session.py is a different beast — it owns
its own pool for ORM/fastapi-users traffic. asyncpg is used directly by
repositories that need pgvector / TEXT[] / JSONB / FTS support that the ORM
doesn't express well.
"""
from __future__ import annotations

import asyncio
import os

import asyncpg


# asyncpg expects plain "postgresql://", not "postgresql+asyncpg://"
_RAW_DB_URL = os.environ["DATABASE_URL"]
_DB_URL = _RAW_DB_URL.replace("+asyncpg", "")

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """Lazy-init the shared pool on first use."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    """For lifespan shutdown hooks."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
