# How RAG works here

What kind of RAG the Maintainer's Copilot runs, where it lives in the repo,
and where it gets evaluated. For the system-wide picture see
[ARCH.md](ARCH.md); for eval methodology see [EVALS.md](EVALS.md).

---

## 1. Which RAG techniques we use

The retrieval pipeline is **hybrid + reranked** — not a single vector lookup.
One question flows through these stages:

```
question
   │
   ├─(optional) HyDE query rewrite ........... query transformation  [stub]
   │
   ├── dense retrieval ....... pgvector cosine search on bge-base embeddings
   ├── sparse retrieval ...... Postgres full-text search (tsvector / tsquery)
   │
   ▼
weighted Reciprocal Rank Fusion ............... merge dense + sparse rankings
   │
   ▼
cross-encoder rerank .......................... bge-reranker-base on top-N
   │
   ▼
top-k chunks
```

| Technique | What it is | How / where |
|---|---|---|
| **Chunking** | Corpus split into ~500-word chunks, 50-word overlap | `rag_ingest.py` → `chunk_text()` |
| **Dense retrieval** | Vector similarity over `bge-base-en-v1.5` (768-dim) embeddings, cosine distance via pgvector's `<=>` | `rag_retrieval.py` → `dense_search()` |
| **Sparse retrieval** | Keyword search — Postgres FTS, `to_tsvector` / `plainto_tsquery`, ranked by `ts_rank_cd` | `rag_retrieval.py` → `sparse_search()` |
| **Hybrid fusion** | Weighted Reciprocal Rank Fusion (RRF) merges the two ranked lists — `k=60`, dense weight `1.0`, sparse weight `0.5` | `rag_retrieval.py` → `weighted_rrf()` |
| **Reranking** | A cross-encoder (`bge-reranker-base`) re-scores the fused top-N (≤20) for final ordering | `rag_retrieval.py` → `rerank_results()` |
| **Metadata filtering** | Optional filters: `content_type`, `label`, `closed_at`, breadcrumb prefix | `rag_retrieval.py` → `_add_filters()` |
| **HyDE** | Hypothetical Document Embeddings — rewrite the query into a hypothetical answer before dense search | `rag_retrieval.py` → `hyde_rewrite()` — **currently a stub** (`use_hyde=False` by default; returns the query unchanged). Prompt drafted in `prompts/hyde.md`. |

So the **active** pipeline is: *dense + sparse → RRF → cross-encoder rerank*.
HyDE is wired in as an optional, off-by-default stage.

Embeddings: **`BAAI/bge-base-en-v1.5`** (768-dim) — the same model embeds the
corpus at ingest, the query at search time, and `memories` rows. Reranker:
**`BAAI/bge-reranker-base`**.

---

## 2. Which folders / files hold the RAG

### Retrieval & ingestion — `model_server/`

| File | Role |
|---|---|
| `model_server/rag_retrieval.py` | The pipeline: `dense_search`, `sparse_search`, `weighted_rrf`, `rerank_results`, `hyde_rewrite`, and the `retrieve()` entry point. Owns the embedder + reranker + the asyncpg pool. |
| `model_server/rag_ingest.py` | Corpus ingestion — chunk → embed → insert into the `chunks` table. |
| `model_server/main.py` | Exposes `GET /rag/search` (and `POST /embed`). |
| `model_server/search_chunks.py`, `find_chunks.py`, `list_docs.py` | Corpus inspection / debugging helpers. |

### API side — `app/`

| File | Role |
|---|---|
| `app/infra/model_server.py` | `rag_search()` adapter — the api's HTTP client to `/rag/search`. |
| `app/services/chatbot.py` | Dispatches the `search_knowledge` tool → `model_server.rag_search`; snapshots results to MinIO. |
| `tools/registry.py` | The `search_knowledge` tool schema the LLM sees. |

### Prompts & corpus

| Path | Role |
|---|---|
| `prompts/hyde.md` | HyDE query-rewrite prompt. |
| `docs/rag_docs/` | The documentation corpus (e.g. `terraform_basics.md`) ingested into `chunks`. |

### Database

| Path | Role |
|---|---|
| `migrations/versions/002_add_chunks_table.py` | Creates the `chunks` table — `embedding vector(768)` (pgvector, HNSW cosine index) + a tsvector FTS index. |

The `chunks` table is the RAG store: one row per chunk, with `text`,
`embedding`, `content_type` (`docs` / `issue`), `source_id`, `chunk_index`,
and a `metadata` JSON blob.

---

## 3. Where RAG is evaluated

All RAG evaluation lives under **`evals/rag/`**, orchestrated by
`evals/run_all.py` and gated in CI. Full methodology is in
[EVALS.md](EVALS.md); the map:

| Path | Role |
|---|---|
| `evals/rag/golden.jsonl` | The golden set — **25** `question` / `ideal_answer` / `chunk_ids` triples (production corpus). |
| `evals/rag/run.py` | The RAG suite — **retrieval** metrics (Hit@5, MRR@10) + **generation** metrics. |
| `evals/rag/generation.py` | Generates an answer from retrieved context and runs the judge calibration. |
| `evals/rag/judge.py` | The frozen LLM judge (faithfulness / relevancy / correctness). |
| `evals/rag/human_labels.jsonl` | 5 hand-labelled answers — the judge↔human agreement check. |
| `evals/rag/run_naive.py` | Dense-only baseline (no fusion, no rerank) — shows what the hybrid pipeline buys. |
| `evals/rag/validate_golden.py` | Sanity-checks the golden JSONL. |
| `evals/fixtures/` | Small CI corpus + 6-question golden so CI needs no production data. |
| `evals/diff.py` + `eval_thresholds.yaml` | Regression gating — fails the build if metrics drop. |

**When it runs:** `.github/workflows/eval.yml` runs the RAG (and
classification) suite on **every push and PR**. `eval_report.json` is diffed
against the last green `main` build; a regression past the thresholds blocks
the merge.

**Run it locally:**

```bash
# RAG suite — retrieval + generation + judge calibration
docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py

# Dense-only baseline for comparison
docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run_naive.py
```

---

## 4. Metrics at a glance

| Side | Metric | Meaning |
|---|---|---|
| Retrieval | **Hit@5** | a ground-truth chunk is in the top 5 |
| Retrieval | **MRR@10** | reciprocal rank of the first ground-truth chunk |
| Generation | **faithfulness** | answer grounded in retrieved context (anti-hallucination) |
| Generation | **answer_relevancy** | answer addresses the question |
| Generation | **answer_correctness** | answer agrees with the golden `ideal_answer` |
| Judge trust | **judge_human_agreement** | how often the judge matches the 5 hand labels |

See [EVALS.md](EVALS.md) for how each is computed and which ones gate CI.
