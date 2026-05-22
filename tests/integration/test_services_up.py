"""Integration tests against a running stack.

Every test here SKIPS cleanly when the service it needs isn't reachable, so
the suite is green on a laptop with nothing running and meaningful in Docker
/ CI where the stack is up. Point them at a real stack by exporting
DATABASE_URL / MODEL_SERVER_URL / API_BASE_URL — conftest.py supplies
localhost defaults.

Run just this tier with:  pytest tests/integration
"""
from __future__ import annotations

import asyncio
import os

import pytest

# Tables every migration head should have produced.
EXPECTED_TABLES = {"users", "audit_log", "chunks", "memories", "widget_configs"}


def test_postgres_reachable_and_migrated():
    asyncpg = pytest.importorskip("asyncpg")
    # asyncpg speaks plain libpq URLs — drop the SQLAlchemy driver suffix.
    url = os.environ["DATABASE_URL"].replace("+asyncpg", "")

    async def check():
        try:
            conn = await asyncpg.connect(url, timeout=3)
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as e:
            pytest.skip(f"no Postgres reachable: {e}")
        try:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            tables = {r["tablename"] for r in rows}
        finally:
            await conn.close()

        assert "alembic_version" in tables, "DB is up but migrations never ran"
        missing = EXPECTED_TABLES - tables
        assert not missing, f"DB reachable but missing tables: {missing}"

    asyncio.run(check())


def test_model_server_health():
    httpx = pytest.importorskip("httpx")
    url = os.environ["MODEL_SERVER_URL"].rstrip("/") + "/health"
    try:
        resp = httpx.get(url, timeout=3)
    except httpx.HTTPError as e:
        pytest.skip(f"model-server not reachable: {e}")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"


def test_api_health():
    httpx = pytest.importorskip("httpx")
    base = os.environ.get("API_BASE_URL", "http://localhost:8000")
    try:
        resp = httpx.get(base.rstrip("/") + "/health", timeout=3)
    except httpx.HTTPError as e:
        pytest.skip(f"api not reachable: {e}")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"
