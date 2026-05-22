"""Frozen LLM judge for RAG generation quality.

We picked a *frozen judge model* over RAGAS for the generation-side metrics
(the question gave the choice). Reasons:

  * RAGAS is already a declared dep, but its scoring is itself an LLM call
    wrapped in a fast-moving 0.x API — pinning *that* is harder than pinning
    our own one-call judge.
  * A hand-written judge prompt is auditable and calibratable: we hand-label
    5 answers (evals/rag/human_labels.jsonl) and measure judge↔human
    agreement every run. You can't calibrate a black box you don't control.
  * "Frozen" here means: a pinned model id, deterministic decoding
    (temperature 0, fixed seed), and a versioned prompt. Bump
    JUDGE_PROMPT_VERSION whenever the rubric below changes so reports stay
    comparable.

The judge scores three axes on a 0-4 integer scale (LLMs are far more
consistent on a short integer scale than on a 0.0-1.0 continuum); the score
is normalised to 0.0-1.0 buckets {0, .25, .5, .75, 1}. The same buckets are
used for the human labels, which makes "within one bucket" a meaningful
agreement test.
"""
from __future__ import annotations

import json
import os

from groq import Groq

# ── Frozen judge configuration ─────────────────────────
# Pinned: a larger model judging the 8B generator's output. Changing either
# of these makes scores incomparable to past reports — bump the version.
JUDGE_MODEL = "llama-3.3-70b-versatile"
JUDGE_PROMPT_VERSION = "v1"
_JUDGE_SEED = 0

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


def judge_available() -> bool:
    """The generation eval only runs when a Groq key is present. Without one
    the RAG suite still reports retrieval metrics — generation is marked
    skipped rather than failing the build."""
    return bool(os.environ.get("GROQ_API_KEY"))


# ── Rubric ─────────────────────────────────────────────
# Kept verbatim in the prompt. Editing it = bump JUDGE_PROMPT_VERSION.
_RUBRIC = """\
You are a strict evaluator of a retrieval-augmented answer. Score THREE axes,
each as an integer 0-4. Be calibrated: reserve 4 for genuinely flawless, use
0 for total failure.

faithfulness — Is every factual claim in the ANSWER supported by the CONTEXT?
  4 = every claim is grounded in the context
  2 = roughly half the claims are grounded, or one notable unsupported claim
  0 = the answer is largely fabricated or contradicts the context

answer_relevancy — Does the ANSWER directly and completely address the QUESTION?
  4 = fully on-point and complete
  2 = partially addresses the question, or padded with irrelevant material
  0 = off-topic; answers a different question

answer_correctness — Does the ANSWER agree with the REFERENCE answer?
  4 = fully consistent with the reference
  2 = partially correct, or correct but missing key points
  0 = contradicts the reference

Respond with ONLY a JSON object, no prose:
{"faithfulness": {"score": <0-4>, "reason": "<short>"},
 "answer_relevancy": {"score": <0-4>, "reason": "<short>"},
 "answer_correctness": {"score": <0-4>, "reason": "<short>"}}"""

_AXES = ("faithfulness", "answer_relevancy", "answer_correctness")


def _build_messages(question: str, answer: str, contexts: list[str],
                     ideal_answer: str) -> list[dict]:
    context_block = "\n\n".join(
        f"[chunk {i + 1}]\n{c}" for i, c in enumerate(contexts)
    ) or "(no context retrieved)"
    user = (
        f"QUESTION:\n{question}\n\n"
        f"CONTEXT:\n{context_block}\n\n"
        f"REFERENCE ANSWER:\n{ideal_answer}\n\n"
        f"ANSWER UNDER TEST:\n{answer}"
    )
    return [
        {"role": "system", "content": _RUBRIC},
        {"role": "user", "content": user},
    ]


def _parse(raw: str) -> dict[str, float] | None:
    """Normalise the judge's 0-4 integer scores to 0.0-1.0 buckets.

    Tolerates both the documented shape ({"faithfulness": {"score": 3}}) and
    the model occasionally flattening it to a bare number
    ({"faithfulness": 3})."""
    try:
        data = json.loads(raw)
        out: dict[str, float] = {}
        for axis in _AXES:
            node = data[axis]
            raw_score = node["score"] if isinstance(node, dict) else node
            score = max(0, min(4, int(raw_score)))  # clamp defensively
            out[axis] = score / 4.0
        return out
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def score_answer(question: str, answer: str, contexts: list[str],
                 ideal_answer: str) -> dict[str, float] | None:
    """Score one answer. Returns {faithfulness, answer_relevancy,
    answer_correctness} in [0,1], or None if the judge call/parse failed
    twice (the caller drops None items from the means and counts them)."""
    messages = _build_messages(question, answer, contexts, ideal_answer)
    for _attempt in range(2):
        try:
            resp = _get_client().chat.completions.create(
                model=JUDGE_MODEL,
                messages=messages,
                temperature=0,
                seed=_JUDGE_SEED,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            parsed = _parse(resp.choices[0].message.content or "")
            if parsed is not None:
                return parsed
        except Exception:
            continue
    return None
