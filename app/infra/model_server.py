# app/infra/model_server.py
"""HTTP adapter for the model-server service.

Every call from `app/` into the model-server (classify, NER, summarize,
embed, RAG search) goes through this module. Centralizing it means:

  - one place to change the base URL (or timeout, or auth scheme)
  - service code doesn't repeat httpx boilerplate
  - tool failures degrade gracefully (each call returns either the real
    response dict, or an `{"error": "..."}` dict the caller can pass to
    the LLM/user without crashing the turn)
  - `embed()` returns a usable zero-vector fallback so memory writes survive
    a brief model-server outage
"""
from __future__ import annotations

import os

import httpx


MODEL_SERVER_URL = os.environ.get("MODEL_SERVER_URL", "http://model-server:8001")

# Embedding dim used by both chunks.embedding and memories.embedding
# (bge-base-en-v1.5).
EMBEDDING_DIM = 768

# Per-call timeouts. summarize + embed touch the bge models which can be
# slow under cold-start; everything else is fast.
_DEFAULT_TIMEOUT = 10.0
_EMBED_TIMEOUT = 5.0


# ── /classify ──────────────────────────────────────────
async def classify(text: str) -> dict:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(f"{MODEL_SERVER_URL}/classify", json={"text": text})
        return r.json() if r.status_code == 200 else {"error": "classifier unavailable"}


# ── /extract (NER) ─────────────────────────────────────
async def extract_entities(text: str) -> dict:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(f"{MODEL_SERVER_URL}/extract", json={"text": text})
        return r.json() if r.status_code == 200 else {"error": "NER unavailable"}


# ── /summarize ─────────────────────────────────────────
async def summarize(text: str) -> dict:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(f"{MODEL_SERVER_URL}/summarize", json={"text": text})
        return r.json() if r.status_code == 200 else {"error": "summarizer unavailable"}


# ── /embed ─────────────────────────────────────────────
async def embed(text: str) -> list[float]:
    """Return a single 768-dim vector. On any failure (timeout, 5xx, etc.)
    falls back to a zero vector so callers don't crash — memory writes are
    still persisted, they just won't be retrievable by similarity until the
    next refresh."""
    try:
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
            r = await client.post(f"{MODEL_SERVER_URL}/embed", json={"texts": [text]})
            if r.status_code == 200:
                return r.json()["embeddings"][0]
    except Exception:
        pass
    return [0.0] * EMBEDDING_DIM


# ── /rag/search ────────────────────────────────────────
async def rag_search(query: str, content_type: str | None = None, top_k: int = 5) -> dict:
    """Hybrid retrieval (dense + sparse + RRF + rerank) over the chunks table.
    Returns `{"results": [...]}` on success or `{"error": ...}` on failure."""
    params: dict = {"query": query, "top_k": top_k}
    if content_type:
        params["content_type"] = content_type
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.get(f"{MODEL_SERVER_URL}/rag/search", params=params)
        return r.json() if r.status_code == 200 else {"error": "RAG search unavailable"}
