"""Unit tests for the eval regression-diff helpers (evals/diff.py).

diff.py is what blocks a merge on a metric regression, so its building
blocks — loading a report, extracting a suite's metrics — are worth
pinning. The CLI/threshold arithmetic is integration-tested by CI itself.
"""
from __future__ import annotations

import json

import pytest

# diff.py imports pyyaml.
pytest.importorskip("yaml")

from evals.diff import _load_report, _metrics  # noqa: E402


def test_load_report_returns_empty_for_missing_file(tmp_path):
    assert _load_report(tmp_path / "does_not_exist.json") == {}


def test_load_report_reads_existing_json(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"git_sha": "abc1234"}))
    assert _load_report(path)["git_sha"] == "abc1234"


def test_metrics_returns_metrics_for_an_ok_suite():
    report = {"suites": {"rag": {"status": "ok", "metrics": {"hit_at_5": 0.9}}}}
    assert _metrics(report, "rag") == {"hit_at_5": 0.9}


@pytest.mark.parametrize("status", ["error", "skipped"])
def test_metrics_is_empty_for_a_non_ok_suite(status):
    # A skipped/errored suite must never count as a regression source.
    report = {"suites": {"rag": {"status": status, "metrics": {"hit_at_5": 0.9}}}}
    assert _metrics(report, "rag") == {}


def test_metrics_is_empty_for_a_missing_suite():
    assert _metrics({"suites": {}}, "rag") == {}
    assert _metrics({}, "rag") == {}
