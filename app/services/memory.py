# app/services/memory.py
import os
import json
import asyncpg
from app.domain.exceptions import DomainError

# asyncpg requires plain "postgresql://", not "postgresql+asyncpg://"
_RAW_DB_URL = os.environ["DATABASE_URL"]
_DB_URL = _RAW_DB_URL.replace("+asyncpg", "")

def _embedding_to_str(embedding: list[float]) -> str:
    """Convert a list of floats to a pgvector-compatible string."""
    return '[' + ','.join(map(str, embedding)) + ']'

async def write_memory(user_id: str, conversation_id: str, summary: str, entities: list[str] | None = None):
    """Store an episodic memory with a zero-vector fallback."""
    import httpx
    embedding = None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://model-server:8001/embed",
                json={"texts": [summary]}
            )
            if resp.status_code == 200:
                embedding = resp.json()["embeddings"][0]
    except Exception:
        pass

    if embedding is None:
        embedding = [0.0] * 768

    emb_str = _embedding_to_str(embedding)
    conn = await asyncpg.connect(_DB_URL)
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
    except Exception as e:
        raise DomainError(f"Failed to write memory: {e}")
    finally:
        await conn.close()

async def recall_memories(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Find relevant memories for the user using vector similarity."""
    import httpx
    embedding = None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://model-server:8001/embed",
                json={"texts": [query]}
            )
            if resp.status_code == 200:
                embedding = resp.json()["embeddings"][0]
    except Exception:
        pass

    if embedding is None:
        embedding = [0.0] * 768

    emb_str = _embedding_to_str(embedding)
    conn = await asyncpg.connect(_DB_URL)
    try:
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
    finally:
        await conn.close()

async def list_memories(user_id: str) -> list[dict]:
    """Return all episodic memories for the given user (most recent first)."""
    conn = await asyncpg.connect(_DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT summary, entities, created_at FROM memories WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50",
            user_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()