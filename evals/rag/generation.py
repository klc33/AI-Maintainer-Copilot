"""Generation-side RAG eval: generate an answer for each golden question
from its retrieved context, then score it with the frozen judge.

Also runs the judge *calibration*: 5 of the 25 golden questions have a
hand-labelled candidate answer in evals/rag/human_labels.jsonl. We judge
those same fixed answers and report how closely the judge agrees with the
human labels. That's the trust check on the judge — without it the
generation scores are just one model's opinion.

Both entry points are synchronous (the Groq client is sync); evals/rag/run.py
calls them from inside its async body, which is fine for a batch eval.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from groq import Groq

from evals.rag.judge import score_answer

# The app generates with llama-3.1-8b-instant (see model_server/summarizer.py
# and the chatbot). The eval generator mirrors that so we measure the model
# we actually ship, not a stand-in.
GEN_MODEL = "llama-3.1-8b-instant"

HUMAN_LABELS_PATH = Path(__file__).resolve().parent / "human_labels.jsonl"

# How many retrieved chunks are fed to the generator. Matches the chatbot's
# RAG tool default.
_TOP_K_CONTEXT = 5

# Two human/judge bucket values count as agreeing if within one 0.25 bucket.
_AGREEMENT_TOL = 0.26

_AXES = ("faithfulness", "answer_relevancy", "answer_correctness")

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


# ── Answer generation ──────────────────────────────────
_GEN_SYSTEM = (
    "You answer questions about HashiCorp Terraform for project maintainers. "
    "Use ONLY the provided context. If the context does not contain the "
    "answer, say you don't know. Be concise — 2-4 sentences."
)


def generate_answer(question: str, context_texts: list[str]) -> str:
    """Single-shot RAG generation: context + question -> answer."""
    context_block = "\n\n".join(
        f"[chunk {i + 1}]\n{t}" for i, t in enumerate(context_texts)
    ) or "(no context retrieved)"
    resp = _get_client().chat.completions.create(
        model=GEN_MODEL,
        messages=[
            {"role": "system", "content": _GEN_SYSTEM},
            {"role": "user",
             "content": f"CONTEXT:\n{context_block}\n\nQUESTION:\n{question}"},
        ],
        temperature=0,
        seed=0,
        max_tokens=300,
    )
    return (resp.choices[0].message.content or "").strip()


# ── Generation eval over the golden set ────────────────
def run_generation_eval(retrieved_per_q: list[tuple[dict, list[dict]]]) -> dict:
    """For each (golden item, retrieved results) pair: generate an answer
    from the top-K context, judge it, and average the judge scores.

    Returns gen_* metrics. If the judge produced no usable scores at all the
    gen_* metrics are omitted and `generation_error` is set instead, so a
    judge outage can't masquerade as a quality regression."""
    totals = {axis: 0.0 for axis in _AXES}
    scored = 0
    errors = 0

    for item, results in retrieved_per_q:
        question = item["question"]
        ideal = item.get("ideal_answer", "")
        contexts = [r["text"] for r in results[:_TOP_K_CONTEXT]]
        try:
            answer = generate_answer(question, contexts)
        except Exception:
            errors += 1
            continue
        verdict = score_answer(question, answer, contexts, ideal)
        if verdict is None:
            errors += 1
            continue
        for axis in _AXES:
            totals[axis] += verdict[axis]
        scored += 1

    if scored == 0:
        return {"generation_error": f"judge produced no scores ({errors} failures)"}

    return {
        "gen_faithfulness": round(totals["faithfulness"] / scored, 4),
        "gen_answer_relevancy": round(totals["answer_relevancy"] / scored, 4),
        "gen_answer_correctness": round(totals["answer_correctness"] / scored, 4),
        "gen_num_scored": scored,
        "gen_num_errors": errors,
    }


# ── Judge calibration against the hand labels ──────────
def _load_human_labels() -> list[dict]:
    with open(HUMAN_LABELS_PATH, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_calibration() -> dict:
    """Judge the 5 hand-labelled candidate answers and compare to the human
    scores. Reports:
      judge_human_mae        — mean |human - judge| over all axis/item pairs
      judge_human_agreement  — fraction of pairs within one 0.25 bucket
    plus a per-item breakdown under `calibration` for eval_report.json."""
    labels = _load_human_labels()
    abs_errors: list[float] = []
    agree = 0
    pairs = 0
    items_detail: list[dict] = []

    for entry in labels:
        human = entry["human_scores"]
        verdict = score_answer(
            question=entry["question"],
            answer=entry["candidate_answer"],
            contexts=entry["contexts"],
            ideal_answer=entry["ideal_answer"],
        )
        item: dict = {
            "id": entry.get("id", entry["question"][:40]),
            "human": human,
            "judge": verdict,
        }
        if verdict is None:
            item["note"] = "judge failed on this item"
            items_detail.append(item)
            continue
        for axis in _AXES:
            diff = abs(human[axis] - verdict[axis])
            abs_errors.append(diff)
            pairs += 1
            if diff <= _AGREEMENT_TOL:
                agree += 1
        items_detail.append(item)

    if pairs == 0:
        return {"calibration": {"error": "judge failed on every calibration item",
                                "items": items_detail}}

    return {
        "judge_human_mae": round(sum(abs_errors) / pairs, 4),
        "judge_human_agreement": round(agree / pairs, 4),
        "calibration": {
            "n_items": len(labels),
            "n_pairs": pairs,
            "tolerance": _AGREEMENT_TOL,
            "items": items_detail,
        },
    }
