"""Smoke test: the FastAPI `api` app imports and wires its routers.

This imports the real `app.main`. Boot checks (Vault, DB, secrets) live in
the lifespan handler and do NOT run on import, so this needs no stack — only
the api dependency group installed. If a dependency is missing the test
skips; any other failure is a genuine wiring regression and fails.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")  # the async engine is built at import time


def _import_app_main():
    """Import app.main, or skip if an app dependency group isn't installed."""
    try:
        import app.main as app_main
    except ModuleNotFoundError as e:
        pytest.skip(f"app dependency not installed: {e.name}")
    return app_main


def test_api_app_object_constructs():
    from fastapi import FastAPI

    app_main = _import_app_main()
    assert isinstance(app_main.app, FastAPI)


def test_api_registers_routes_including_health():
    app_main = _import_app_main()
    paths = {getattr(route, "path", None) for route in app_main.app.routes}
    assert "/health" in paths
    # The six routers from app/api/ should contribute more than just /health.
    assert len(paths) > 5, f"suspiciously few routes registered: {sorted(paths)}"


def test_health_handler_returns_ok():
    # Call the coroutine directly — no TestClient, so the lifespan/boot
    # checks never run.
    app_main = _import_app_main()
    assert asyncio.run(app_main.health()) == {"status": "ok"}
