# app/db/session.py
"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

This is the canonical home for the SQLAlchemy plumbing. `app/infra/db.py`
re-exports from here for backward compatibility with existing imports
(`from app.infra.db import engine, Base, async_session_factory`).
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = os.environ["DATABASE_URL"]

# pool_pre_ping discards stale connections (e.g. after a DB restart) instead
# of failing the next query — costs one cheap SELECT 1 per checkout.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    """Declarative base for every ORM model in app.db.models."""
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields one transactional session per request.

    Usage:
        @router.get("/widgets/{wid}")
        async def fetch(wid: str, session: AsyncSession = Depends(get_session)):
            ...
    """
    async with async_session_factory() as session:
        yield session
