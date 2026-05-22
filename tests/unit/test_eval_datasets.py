"""Unit tests guarding the eval *data* files.

The golden set, the hand-labelled judge-calibration set, and the regression
thresholds are hand-edited JSON/YAML — a stray comma or an off-scale score
silently weakens the eval. These tests are the schema check.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_AXES = {"faithfulness", "answer_relevancy", "answer_correctness"}
_SCORE_BUCKETS = {0.0, 0.25, 0.5, 0.75, 1.0}


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_rag_golden_set_has_25_well_formed_triples():
    rows = _read_jsonl(_ROOT / "evals" / "rag" / "golden.jsonl")
    assert len(rows) == 25, "the brief calls for exactly 25 golden triples"
    for i, row in enumerate(rows, 1):
        assert {"question", "ideal_answer", "chunk_ids"} <= set(row), f"line {i}"
        assert row["question"].strip(), f"line {i}: empty question"
        assert row["ideal_answer"].strip(), f"line {i}: empty ideal_answer"
        assert isinstance(row["chunk_ids"], list) and row["chunk_ids"], (
            f"line {i}: chunk_ids must be a non-empty list"
        )
        assert all(isinstance(c, int) for c in row["chunk_ids"]), (
            f"line {i}: chunk_ids must be integers"
        )


def test_ci_fixture_golden_set_is_well_formed():
    rows = _read_jsonl(_ROOT / "evals" / "fixtures" / "golden.jsonl")
    assert rows, "fixture golden set is empty"
    for row in rows:
        assert {"question", "ideal_answer", "chunk_ids"} <= set(row)


def test_human_label_set_has_5_calibration_items():
    rows = _read_jsonl(_ROOT / "evals" / "rag" / "human_labels.jsonl")
    assert len(rows) == 5, "the brief calls for 5 hand-labelled answers"
    for row in rows:
        assert {
            "id",
            "question",
            "candidate_answer",
            "contexts",
            "ideal_answer",
            "human_scores",
        } <= set(row), f"{row.get('id', '?')}: missing required keys"
        assert isinstance(row["contexts"], list) and row["contexts"]
        assert set(row["human_scores"]) == _AXES, f"{row['id']}: wrong score axes"
        for axis, score in row["human_scores"].items():
            assert score in _SCORE_BUCKETS, (
                f"{row['id']}.{axis}={score} is off the 0/.25/.5/.75/1 scale"
            )


def test_human_label_ids_are_unique():
    rows = _read_jsonl(_ROOT / "evals" / "rag" / "human_labels.jsonl")
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)), "duplicate id in human_labels.jsonl"


def test_eval_thresholds_have_a_min_absolute_floor():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((_ROOT / "eval_thresholds.yaml").read_text())
    assert "rag" in data and "classification" in data
    for suite, metrics in data.items():
        for metric, gates in metrics.items():
            assert "min_absolute" in gates, (
                f"{suite}.{metric} has no min_absolute floor"
            )
