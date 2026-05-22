"""Unit tests for the frozen RAG judge's pure logic (evals/rag/judge.py).

Covers score parsing/normalisation and the frozen-config guard. The live
LLM call (`score_answer`) is not exercised here — that needs a Groq key and
is run by the eval suite, not the unit tier.
"""
from __future__ import annotations

import json

import pytest

# judge.py imports the Groq client at module load.
pytest.importorskip("groq")

from evals.rag.judge import (  # noqa: E402
    JUDGE_MODEL,
    JUDGE_PROMPT_VERSION,
    _parse,
    judge_available,
)


def _nested(faith: int, rel: int, corr: int) -> str:
    return json.dumps(
        {
            "faithfulness": {"score": faith, "reason": "x"},
            "answer_relevancy": {"score": rel, "reason": "x"},
            "answer_correctness": {"score": corr, "reason": "x"},
        }
    )


def test_parse_normalises_nested_0_to_4_scores_into_buckets():
    out = _parse(_nested(4, 2, 0))
    assert out == {
        "faithfulness": 1.0,
        "answer_relevancy": 0.5,
        "answer_correctness": 0.0,
    }


def test_parse_accepts_flat_integer_shape():
    # The model occasionally flattens {"score": n} to a bare n.
    out = _parse(
        json.dumps(
            {"faithfulness": 3, "answer_relevancy": 1, "answer_correctness": 2}
        )
    )
    assert out == {
        "faithfulness": 0.75,
        "answer_relevancy": 0.25,
        "answer_correctness": 0.5,
    }


def test_parse_clamps_out_of_range_scores():
    out = _parse(_nested(9, -3, 4))
    assert out["faithfulness"] == 1.0
    assert out["answer_relevancy"] == 0.0
    assert out["answer_correctness"] == 1.0


def test_parse_returns_none_on_invalid_json():
    assert _parse("not json at all") is None


def test_parse_returns_none_when_an_axis_is_missing():
    assert _parse(json.dumps({"faithfulness": {"score": 4}})) is None


def test_judge_available_tracks_the_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert judge_available() is False
    monkeypatch.setenv("GROQ_API_KEY", "gsk_dummy_not_a_real_key")
    assert judge_available() is True


def test_judge_is_frozen():
    # "Frozen" = a pinned model + a versioned rubric. If either changes,
    # past eval reports stop being comparable — so this test forces the
    # change to be deliberate (update the constant *and* this assertion).
    assert JUDGE_MODEL == "llama-3.3-70b-versatile"
    assert JUDGE_PROMPT_VERSION == "v1"
