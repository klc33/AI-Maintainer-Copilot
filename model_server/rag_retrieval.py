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
DB_URL = os.environ.get("DATABASE_URL", "postgresql://copilot:changeme@localhost:5432/copilot")
HYDE_PROMPT = (
    "Given the question about HashiCorp Terraform, write a short passage (3-4 sentences) "
    "that could answer it, as if it were from Terraform documentation."
)

device = "cuda" if __import__("torch").cuda.is_available() else "cpu"
embed_model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
reranker = CrossEncoder(RERANK_MODEL_NAME, device=device)

async def create_pool():
    return await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)

async def dense_search(conn, query_vec, top_k=50, content_types=None, labels=None, min_closed_at=None, breadcrumb_prefix=None):
    """Dense (vector) search with optional metadata filters."""
    conditions = []
    params = [query_vec]
    if content_types:
        conditions.append(f"content_type IN ({','.join('$' + str(i+2+idx) for idx in range(len(content_types)))})")
        params.extend(content_types)
    if labels:
        conditions.append(f"metadata->>'label' IN ({','.join('$' + str(i+2+len(content_types)+idx) for idx in range(len(labels)))})")
        params.extend(labels)
    if min_closed_at:
        conditions.append("metadata->>'closed_at' >= $" + str(len(params)+1))
        params.append(min_closed_at)
    if breadcrumb_prefix:
        conditions.append("metadata->>'breadcrumb' LIKE $" + str(len(params)+1))
        params.append(breadcrumb_prefix + "%")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"""
        SELECT id, text, metadata, 1 - (embedding <=> $1::vector) AS similarity
        FROM chunks
        {where}
        ORDER BY embedding <=> $1::vector
        LIMIT $2
    """
    params.append(top_k)
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

async def sparse_search(conn, query_text, top_k=50, content_types=None, labels=None, min_closed_at=None, breadcrumb_prefix=None):
    """Sparse (full-text) search."""
    conditions = ["to_tsvector('english', text) @@ plainto_tsquery('english', $1)"]
    params = [query_text]
    if content_types:
        conditions.append(f"content_type IN ({','.join('$' + str(i+2+idx) for idx in range(len(content_types)))})")
        params.extend(content_types)
    if labels:
        conditions.append(f"metadata->>'label' IN ({','.join('$' + str(i+2+len(content_types)+idx) for idx in range(len(labels)))})")
        params.extend(labels)
    if min_closed_at:
        conditions.append("metadata->>'closed_at' >= $" + str(len(params)+1))
        params.append(min_closed_at)
    if breadcrumb_prefix:
        conditions.append("metadata->>'breadcrumb' LIKE $" + str(len(params)+1))
        params.append(breadcrumb_prefix + "%")

    where = "WHERE " + " AND ".join(conditions)
    query = f"""
        SELECT id, text, metadata, ts_rank_cd(to_tsvector('english', text), plainto_tsquery('english', $1)) AS score
        FROM chunks
        {where}
        ORDER BY score DESC
        LIMIT $2
    """
    params.append(top_k)
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