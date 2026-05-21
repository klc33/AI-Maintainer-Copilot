# app/repositories/memories.py
"""All SQL against the `memories` table lives here. The service layer
(`app/services/memory.py`) is responsible for computing the embedding;
this module only persists / fetches rows.

The pgvector value is passed in as a `list[float]`; we serialize it to
the `[x, y, z]` literal pgvector accepts.
"""
from __future__ import annotations

import json
from typing import Iterable

from app.db.pool import get_pool


def _embedding_to_str(embedding: Iterable[float]) -> str:
    return "[" + ",".join(map(str, embedding)) + "]"


async def exists(user_id: str, summary: str) -> int | None:
    """Idempotency check: return the existing id if a memory with the same
    (user_id, summary) already exists, else None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM memories WHERE user_id = $1 AND summary = $2 LIMIT 1",
            user_id, summary,
        )


async def insert(
    *,
    user_id: str,
    conversation_id: str,
    summary: str,
    entities: list[str] | None,
    embedding: list[float],
) -> int:
    """Insert one episodic memory. Returns the new row id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO memories (user_id, conversation_id, memory_type, summary, entities, embedding)
                VALUES ($1, $2, 'episodic', $3, $4::json, $5::vector)
                RETURNING id
                """,
                user_id, conversation_id, summary,
                json.dumps(entities or []), _embedding_to_str(embedding),
            )
            return row["id"]


async def recall_by_similarity(user_id: str, embedding: list[float], top_k: int) -> list[dict]:
    """Return the `top_k` memories for this user sorted by cosine similarity
    to the query embedding. Includes the similarity score (1 - distance)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT summary, entities, 1 - (embedding <=> $1::vector) AS similarity
            FROM memories
            WHERE user_id = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            _embedding_to_str(embedding), user_id, top_k,
        )
        return [dict(r) for r in rows]


async def list_for_user(user_id: str, limit: int = 50) -> list[dict]:
    """Most-recent-first listing for the Memory Inspector page."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT summary, entities, created_at FROM memories "
            "WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id, limit,
        )
        return [dict(r) for r in rows]
