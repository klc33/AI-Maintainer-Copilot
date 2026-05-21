"""RAG evaluation harness — Hit@5 and MRR@10 against a golden JSONL.

Used by both the orchestrator (evals/run_all.py) and as a CLI for local
manual runs:

    docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py

Golden file path is overridable via $RAG_GOLDEN_PATH (defaults to
evals/rag/golden.jsonl). Each line: {"question": str, "chunk_ids": [int, ...]}.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path


def _golden_path() -> Path:
    override = os.environ.get("RAG_GOLDEN_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "golden.jsonl"


async def run(golden_path: Path | None = None) -> dict:
    """Return {'hit_at_5': float, 'mrr_at_10': float, 'num_questions': int}.

    Imports rag_retrieval lazily so this module can be imported in
    environments that don't have sentence-transformers (the orchestrator
    will skip if the import fails)."""
    # Importing model_server only when actually running the eval keeps
    # `evals/run_all.py --suites classification` cheap.
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
    from model_server.rag_retrieval import get_pool, retrieve

    path = golden_path or _golden_path()
    with open(path, "r", encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    pool = await get_pool()
    hit_at_5 = 0
    mrr_sum = 0.0
    total = 0

    async with pool.acquire() as conn:
        for item in golden:
            question = item["question"]
            ground_truth = set(item["chunk_ids"])
            results = await retrieve(conn, question, top_k=10)

            retrieved_ids = [r["id"] for r in results[:5]]
            if ground_truth.intersection(retrieved_ids):
                hit_at_5 += 1

            for rank, r in enumerate(results[:10], start=1):
                if r["id"] in ground_truth:
                    mrr_sum += 1.0 / rank
                    break

            total += 1

    return {
        "hit_at_5": (hit_at_5 / total) if total else 0.0,
        "mrr_at_10": (mrr_sum / total) if total else 0.0,
        "num_questions": total,
    }


async def main() -> None:
    metrics = await run()
    report = dict(metrics)
    os.makedirs("datasets", exist_ok=True)
    with open("datasets/rag_eval_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"Hit@5: {metrics['hit_at_5']:.4f}  |  MRR@10: {metrics['mrr_at_10']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
