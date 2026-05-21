# app/services/memory.py
"""Episodic memory business logic.

Service-layer concerns only:
  - compute the embedding for a summary (HTTP call to model-server /embed)
  - enforce idempotency on (user_id, summary)
  - persist via the memory repository
  - write an audit_log entry on every successful write

All SQL lives in `app.repositories.memories` and `app.repositories.audit_log`.
"""
from __future__ import annotations

import asyncpg

from app.domain.exceptions import DomainError
from app.infra import model_server
from app.infra.redaction import redact, redact_deep
from app.repositories import audit_log as audit_log_repo
from app.repositories import memories as memories_repo


async def write_memory(
    user_id: str,
    conversation_id: str,
    summary: str,
    entities: list[str] | None = None,
) -> None:
    """Idempotent on (user_id, summary). If the row already exists we treat
    the call as a successful no-op (no second audit entry either).

    Both `summary` and `entities` are redacted **before** anything else
    happens — embedding lookup, idempotency check, persistence — so any
    secret the LLM extracted from the user turn never makes it to disk
    or to the model-server."""
    summary = redact(summary)
    entities = redact_deep(entities) if entities is not None else None

    if await memories_repo.exists(user_id, summary):
        return

    embedding = await model_server.embed(summary)
    try:
        memory_id = await memories_repo.insert(
            user_id=user_id,
            conversation_id=conversation_id,
            summary=summary,
            entities=entities,
            embedding=embedding,
        )
    except asyncpg.UniqueViolationError:
        # Lost the race against a concurrent insert; honor idempotency.
        return
    except Exception as e:
        raise DomainError(f"Failed to write memory: {e}")

    await audit_log_repo.record(
        actor_type="user",
        actor_id=user_id,
        action="write_memory",
        target_type="memory",
        target_id=str(memory_id),
    )


async def recall_memories(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Embed the query, fetch top-K memories by cosine similarity."""
    embedding = await model_server.embed(query)
    return await memories_repo.recall_by_similarity(user_id, embedding, top_k)


async def list_memories(user_id: str) -> list[dict]:
    """Most-recent-first listing for the Memory Inspector page."""
    return await memories_repo.list_for_user(user_id)
