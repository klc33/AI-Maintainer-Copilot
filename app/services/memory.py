# app/services/memory.py
import os
import json
import asyncio
import asyncpg
import httpx
from app.domain.exceptions import DomainError

# asyncpg requires plain "postgresql://", not "postgresql+asyncpg://"
_RAW_DB_URL = os.environ["DATABASE_URL"]
_DB_URL = _RAW_DB_URL.replace("+asyncpg", "")

EMBED_URL = os.environ.get("MODEL_SERVER_URL", "http://model-server:8001") + "/embed"
EMBED_DIM = 768

# ── Lazy module-level connection pool ──────────────────
# Previously we opened a fresh asyncpg.connect() on every read/write, which
# burns DB connections under load. One pool, one lock, one event loop binding.
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()

async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=10)
    return _pool


def _embedding_to_str(embedding: list[float]) -> str:
    """Convert a list of floats to a pgvector-compatible string."""
    return "[" + ",".join(map(str, embedding)) + "]"


async def _embed(text: str) -> list[float]:
    """Call the model-server /embed endpoint. Falls back to a zero vector so
    that callers don't crash if the model-server is briefly unavailable; the
    upshot is that memories written during an outage won't be retrievable by
    similarity, which is preferable to losing them entirely."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(EMBED_URL, json={"texts": [text]})
            if resp.status_code == 200:
                return resp.json()["embeddings"][0]
    except Exception:
        pass
    return [0.0] * EMBED_DIM


async def write_memory(user_id: str, conversation_id: str, summary: str, entities: list[str] | None = None):
    """Store an episodic memory. Idempotent on (user_id, summary): if a memory
    with the same exact summary already exists for this user, do nothing. The
    LLM tends to re-fire this tool on every turn that mentions the same fact;
    dedup here means the table doesn't fill up with 11 copies of the same row."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        existing_id = await conn.fetchval(
            "SELECT id FROM memories WHERE user_id = $1 AND summary = $2 LIMIT 1",
            user_id, summary,
        )
        if existing_id is not None:
            return  # already stored; treat as success

        embedding = await _embed(summary)
        emb_str = _embedding_to_str(embedding)
        try:
            async with conn.transaction():
                result = await conn.fetchrow(
                    """
                    INSERT INTO memories (user_id, conversation_id, memory_type, summary, entities, embedding)
                    VALUES ($1, $2, 'episodic', $3, $4::json, $5::vector)
                    RETURNING id
                    """,
                    user_id, conversation_id, summary, json.dumps(entities or []), emb_str,
                )
                memory_id = result["id"]
                await conn.execute(
                    """
                    INSERT INTO audit_log (actor_type, actor_id, action, target_type, target_id)
                    VALUES ('user', $1, 'write_memory', 'memory', $2)
                    """,
                    user_id, str(memory_id),
                )
        except asyncpg.UniqueViolationError:
            # Lost the race against another concurrent insert for the same
            # (user_id, summary); the unique index on the table handles this
            # at the DB layer. Treat as idempotent success.
            return
        except Exception as e:
            raise DomainError(f"Failed to write memory: {e}")


async def recall_memories(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Find relevant memories for the user using vector similarity."""
    embedding = await _embed(query)
    emb_str = _embedding_to_str(embedding)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT summary, entities, 1 - (embedding <=> $1::vector) AS similarity
            FROM memories
            WHERE user_id = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            emb_str, user_id, top_k,
        )
        return [dict(r) for r in rows]


async def list_memories(user_id: str) -> list[dict]:
    """Return all episodic memories for the given user (most recent first)."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT summary, entities, created_at FROM memories WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50",
            user_id,
        )
        return [dict(r) for r in rows]
