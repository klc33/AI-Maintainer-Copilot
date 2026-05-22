"""Smoke tests: the documentation set and docker-compose are present and
parseable. Cheap guards against a doc being deleted or a compose typo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# The README family + the per-feature docs the README links to.
DOCS = [
    "README.md",
    "ARCH.md",
    "EVALS.md",
    "RUNBOOK.md",
    "SECURITY.md",
    "DECISIONS.md",
    "rag-works.md",
    "deliverables.md",
]


@pytest.mark.parametrize("name", DOCS)
def test_doc_exists_and_is_non_empty(name):
    path = _ROOT / name
    assert path.exists(), f"{name} is missing"
    assert path.stat().st_size > 0, f"{name} is empty"


def test_docker_compose_parses_and_declares_core_services():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    services = set(compose.get("services", {}))
    expected = {"db", "redis", "minio", "vault", "api", "model-server"}
    assert expected <= services, f"compose missing services: {expected - services}"
