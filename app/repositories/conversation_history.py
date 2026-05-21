# app/repositories/conversation_history.py
"""Short-term conversation history stored in Redis.

Each conversation maps to a Redis list at `conv:{conversation_id}:msgs`
containing JSON-serialized {role, content} entries. The chatbot service
reads the most recent N before each turn and appends the user+assistant
messages after the turn completes.

Keeping Redis access here lets the service layer talk only in terms of
'load history' / 'persist turn' without knowing about lrange/rpush/ltrim.
"""
from __future__ import annotations

import json
from typing import Iterable

from app.infra.redis import redis_client

# How many of the most recent messages to keep per conversation. 20 turns
# is a balance between giving the LLM context and not blowing past the
# token budget on long-running widget sessions.
HISTORY_MAX_LEN = 20
# Drop the key entirely after this many seconds of inactivity.
HISTORY_TTL_SECONDS = 86_400


def _key(conversation_id: str) -> str:
    return f"conv:{conversation_id}:msgs"


async def load(conversation_id: str) -> list[dict]:
    """Return the conversation's messages in chronological order."""
    raw = await redis_client.lrange(_key(conversation_id), 0, -1)
    return [json.loads(m) for m in raw]


async def append(conversation_id: str, messages: Iterable[dict]) -> None:
    """Append one or more {role, content} messages, trim to HISTORY_MAX_LEN,
    and refresh the TTL. A turn typically appends (user, assistant)."""
    key = _key(conversation_id)
    msgs = list(messages)
    if not msgs:
        return
    pipe = redis_client.pipeline()
    for m in msgs:
        pipe.rpush(key, json.dumps(m))
    pipe.ltrim(key, -HISTORY_MAX_LEN, -1)
    pipe.expire(key, HISTORY_TTL_SECONDS)
    await pipe.execute()


async def clear(conversation_id: str) -> int:
    """Delete the conversation history entirely. Returns 1 if a key was
    removed, else 0. Useful for the planned conversation-deletion endpoint."""
    return await redis_client.delete(_key(conversation_id))
