# model_server/rag_retrieval.py
"""Advanced RAG retrieval: dense, sparse, hybrid (RRF), rerank, HyDE."""
import os
import asyncio
import asyncpg
import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder

# Config
EMBED_MODEL_NAME = "BAAI/bge-base-en-v1.5"
RERANK_MODEL_NAME = "BAAI/bge-reranker-base"
# Accept the same +asyncpg-prefixed URL the api uses, since asyncpg itself
# doesn't understand the SQLAlchemy driver suffix.
_RAW_DB_URL = os.environ.get("DATABASE_URL", "postgresql://copilot:changeme@localhost:5432/copilot")
DB_URL = _RAW_DB_URL.replace("+asyncpg", "")
HYDE_PROMPT = (
    "Given the question about HashiCorp Terraform, write a short passage (3-4 sentences) "
    "that could answer it, as if it were from Terraform documentation."
)

device = "cuda" if __import__("torch").cuda.is_available() else "cpu"
embed_model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
reranker = CrossEncoder(RERANK_MODEL_NAME, device=device)

# ── Lazy module-level pool ─────────────────────────────
# Previously create_pool() was called per /rag/search request and the pool
# was never closed, leaking connections fast.
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
    return _pool

# Kept for backwards compatibility with eval scripts; new callers should use
# get_pool() to share the module-level pool.
async def create_pool():
    return await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)

def _add_filters(params, conditions, content_types, labels, min_closed_at, breadcrumb_prefix):
    """Append asyncpg-style positional placeholders for the optional metadata
    filters used by both dense and sparse search. Mutates params + conditions."""
    if content_types:
        start = len(params) + 1
        params.extend(content_types)
        placeholders = ",".join(f"${start + i}" for i in range(len(content_types)))
        conditions.append(f"content_type IN ({placeholders})")
    if labels:
        start = len(params) + 1
        params.extend(labels)
        placeholders = ",".join(f"${start + i}" for i in range(len(labels)))
        conditions.append(f"metadata->>'label' IN ({placeholders})")
    if min_closed_at:
        params.append(min_closed_at)
        conditions.append(f"metadata->>'closed_at' >= ${len(params)}")
    if breadcrumb_prefix:
        params.append(breadcrumb_prefix + "%")
        conditions.append(f"metadata->>'breadcrumb' LIKE ${len(params)}")


async def dense_search(conn, query_vec, top_k=50, content_types=None, labels=None, min_closed_at=None, breadcrumb_prefix=None):
    """Dense (vector) search with optional metadata filters."""
    conditions = []
    params = [query_vec]
    _add_filters(params, conditions, content_types, labels, min_closed_at, breadcrumb_prefix)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(top_k)
    limit_idx = len(params)
    query = f"""
        SELECT id, text, content_type, source_id, metadata,
               1 - (embedding <=> $1::vector) AS similarity
        FROM chunks
        {where}
        ORDER BY embedding <=> $1::vector
        LIMIT ${limit_idx}
    """
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def sparse_search(conn, query_text, top_k=50, content_types=None, labels=None, min_closed_at=None, breadcrumb_prefix=None):
    """Sparse (full-text) search."""
    conditions = ["to_tsvector('english', text) @@ plainto_tsquery('english', $1)"]
    params = [query_text]
    _add_filters(params, conditions, content_types, labels, min_closed_at, breadcrumb_prefix)
    where = "WHERE " + " AND ".join(conditions)
    params.append(top_k)
    limit_idx = len(params)
    query = f"""
        SELECT id, text, content_type, source_id, metadata,
               ts_rank_cd(to_tsvector('english', text), plainto_tsquery('english', $1)) AS score
        FROM chunks
        {where}
        ORDER BY score DESC
        LIMIT ${limit_idx}
    """
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

def weighted_rrf(dense_results, sparse_results, k=60, w_d=1.0, w_s=0.5):
    """Weighted Reciprocal Rank Fusion."""
    scores = {}
    for rank, item in enumerate(dense_results):
        scores[item['id']] = scores.get(item['id'], 0) + w_d / (k + rank + 1)
    for rank, item in enumerate(sparse_results):
        scores[item['id']] = scores.get(item['id'], 0) + w_s / (k + rank + 1)
    merged_ids = sorted(scores, key=scores.get, reverse=True)
    all_items = {item['id']: item for item in dense_results + sparse_results}
    return [all_items[i] for i in merged_ids if i in all_items]

async def rerank_results(query, candidates, top_k=5):
    """Cross-encoder reranking."""
    pairs = [(query, c['text']) for c in candidates]
    scores = reranker.predict(pairs, batch_size=8)
    scored = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]

async def hyde_rewrite(query, llm_client=None):
    """
    Generate a hypothetical document to improve dense retrieval.
    If no external LLM is provided, returns the original query (no rewrite).
    """
    if llm_client is None:
        return query  # fallback
    # Placeholder: use Groq to generate a hypothetical answer
    # We'll wire this later when chatbot is ready.
    return query  # for now, identity

async def retrieve(conn, query, top_k=5, content_types=None, labels=None, min_closed_at=None, breadcrumb_prefix=None,
                   use_hyde=False, llm_client=None, w_d=1.0, w_s=0.5, k=60):
    """Full retrieval pipeline."""
    # Query transformation (HyDE)
    if use_hyde:
        query_for_dense = await hyde_rewrite(query, llm_client)
    else:
        query_for_dense = query
    query_vec = embed_model.encode(query_for_dense, normalize_embeddings=True).tolist()
    query_vec_str = '[' + ','.join(map(str, query_vec)) + ']'

    # Retrieve
    dense = await dense_search(conn, query_vec_str, top_k=50, content_types=content_types, labels=labels,
                                min_closed_at=min_closed_at, breadcrumb_prefix=breadcrumb_prefix)
    sparse = await sparse_search(conn, query, top_k=50, content_types=content_types, labels=labels,
                                 min_closed_at=min_closed_at, breadcrumb_prefix=breadcrumb_prefix)

    # Fusion
    fused = weighted_rrf(dense, sparse, k=k, w_d=w_d, w_s=w_s)

    # Rerank
    top_n = min(20, len(fused))
    reranked = await rerank_results(query, fused[:top_n], top_k=top_k)

    return reranked