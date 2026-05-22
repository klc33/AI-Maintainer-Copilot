"""RAG evaluation harness — retrieval metrics + generation metrics.

Retrieval:  Hit@5 and MRR@10 against the golden JSONL.
Generation: an answer is generated for every golden question from its
            retrieved context and scored by a frozen LLM judge
            (faithfulness / answer_relevancy / answer_correctness). The
            judge is also calibrated against 5 hand-labelled answers and
            the judge↔human agreement is reported. See evals/rag/judge.py
            and evals/rag/generation.py, and EVALS.md for the methodology.

The generation half needs a Groq key. Without one the suite still reports
retrieval metrics and records `generation: skipped` — it never fails the
build just because the key is absent.

Used by the orchestrator (evals/run_all.py) and as a CLI for local runs:

    docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py

Golden file path is overridable via $RAG_GOLDEN_PATH (defaults to
evals/rag/golden.jsonl). Each line:
{"question": str, "ideal_answer": str, "chunk_ids": [int, ...]}.
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


def _add_generation_metrics(
    metrics: dict, retrieved_per_q: list[tuple[dict, list[dict]]]
) -> None:
    """Best-effort: append generation + calibration metrics to `metrics`.

    Wrapped so a judge outage (no key, Groq down, parse failure) degrades to
    a `generation: skipped` note instead of failing the retrieval suite."""
    try:
        from evals.rag.judge import (
            JUDGE_MODEL,
            JUDGE_PROMPT_VERSION,
            judge_available,
        )
    except Exception as e:  # groq not installed, etc.
        metrics["generation"] = f"skipped: judge import failed ({e})"
        return

    if not judge_available():
        metrics["generation"] = "skipped: GROQ_API_KEY not set"
        return

    try:
        from evals.rag.generation import run_calibration, run_generation_eval

        metrics["judge_model"] = JUDGE_MODEL
        metrics["judge_prompt_version"] = JUDGE_PROMPT_VERSION
        metrics.update(run_generation_eval(retrieved_per_q))
        metrics.update(run_calibration())
    except Exception as e:
        metrics["generation_error"] = str(e)


async def run(golden_path: Path | None = None) -> dict:
    """Return the RAG suite metrics dict.

    Always present: hit_at_5, mrr_at_10, num_questions.
    Present when the judge is available: gen_faithfulness,
    gen_answer_relevancy, gen_answer_correctness, judge_human_mae,
    judge_human_agreement (+ a `calibration` breakdown).

    rag_retrieval is imported lazily so this module stays importable where
    sentence-transformers isn't installed (the orchestrator skips on
    ImportError)."""
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
    # Stash each question's retrieved results so the generation pass reuses
    # them instead of retrieving a second time.
    retrieved_per_q: list[tuple[dict, list[dict]]] = []

    async with pool.acquire() as conn:
        for item in golden:
            question = item["question"]
            ground_truth = set(item["chunk_ids"])
            results = await retrieve(conn, question, top_k=10)
            retrieved_per_q.append((item, results))

            retrieved_ids = [r["id"] for r in results[:5]]
            if ground_truth.intersection(retrieved_ids):
                hit_at_5 += 1

            for rank, r in enumerate(results[:10], start=1):
                if r["id"] in ground_truth:
                    mrr_sum += 1.0 / rank
                    break

            total += 1

    metrics = {
        "hit_at_5": (hit_at_5 / total) if total else 0.0,
        "mrr_at_10": (mrr_sum / total) if total else 0.0,
        "num_questions": total,
    }

    # Generation metrics + judge calibration (best-effort; never fails here).
    _add_generation_metrics(metrics, retrieved_per_q)
    return metrics


async def main() -> None:
    metrics = await run()
    os.makedirs("datasets", exist_ok=True)
    with open("datasets/rag_eval_report.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"Hit@5: {metrics['hit_at_5']:.4f}  |  MRR@10: {metrics['mrr_at_10']:.4f}")
    if "gen_faithfulness" in metrics:
        print(
            f"Generation  faithfulness={metrics['gen_faithfulness']:.3f}  "
            f"relevancy={metrics['gen_answer_relevancy']:.3f}  "
            f"correctness={metrics['gen_answer_correctness']:.3f}  "
            f"(n={metrics.get('gen_num_scored')})"
        )
    if "judge_human_agreement" in metrics:
        print(
            f"Judge<->human  agreement={metrics['judge_human_agreement']:.3f}  "
            f"MAE={metrics['judge_human_mae']:.3f}"
        )
    elif metrics.get("generation"):
        print(f"Generation: {metrics['generation']}")
    elif metrics.get("generation_error"):
        print(f"Generation error: {metrics['generation_error']}")


if __name__ == "__main__":
    asyncio.run(main())
