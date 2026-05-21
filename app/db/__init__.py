# app/db/__init__.py
"""Canonical SQLAlchemy database package: declarative Base, async engine,
session factory + FastAPI dependency, and one ORM model per table.

Common import patterns:

    from app.db import get_session, async_session_factory          # session
    from app.db import Base, engine                                # plumbing
    from app.db.models import User, AuditLog, Chunk, Memory, WidgetConfig

For backward compatibility, `app.infra.db` and `app.domain.models` re-export
the most-used names from here.
"""
from app.db.session import (
    Base,
    async_session_factory,
    engine,
    get_session,
)
from app.db.models import (
    AuditLog,
    Chunk,
    Memory,
    User,
    WidgetConfig,
    EMBEDDING_DIM,
)

__all__ = [
    "Base",
    "async_session_factory",
    "engine",
    "get_session",
    "User",
    "AuditLog",
    "Chunk",
    "Memory",
    "WidgetConfig",
    "EMBEDDING_DIM",
]
