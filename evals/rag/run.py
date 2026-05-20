# evals/rag/run.py
"""RAG evaluation harness for the golden set."""
import json
import asyncio
import asyncpg
import os
from pathlib import Path

# Import your retrieval function
import sys
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from model_server.rag_retrieval import create_pool, retrieve

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.jsonl"

async def main():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    pool = await create_pool()
    hit_at_5 = 0
    mrr_sum = 0.0
    total = 0

    async with pool.acquire() as conn:
        for item in golden:
            question = item["question"]
            ground_truth = set(item["chunk_ids"])
            results = await retrieve(conn, question, top_k=10)

            # Hit@5: at least one ground‑truth chunk in top‑5
            retrieved_ids = [r["id"] for r in results[:5]]
            if ground_truth.intersection(retrieved_ids):
                hit_at_5 += 1

            # MRR: reciprocal rank of first relevant chunk in top‑10
            for rank, r in enumerate(results[:10], start=1):
                if r["id"] in ground_truth:
                    mrr_sum += 1.0 / rank
                    break

            total += 1

    hit_at_5 = hit_at_5 / total if total else 0.0
    mrr = mrr_sum / total if total else 0.0

    report = {
        "hit_at_5": hit_at_5,
        "mrr_at_10": mrr,
        "num_questions": total,
    }

    os.makedirs("datasets", exist_ok=True)
    with open("datasets/rag_eval_report.json", "w") as f:
        json.dump(report, f, indent=2)

    print(f"Hit@5: {hit_at_5:.4f}  |  MRR@10: {mrr:.4f}")

if __name__ == "__main__":
    asyncio.run(main())