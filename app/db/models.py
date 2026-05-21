# app/db/models.py
"""SQLAlchemy ORM models for every table in the copilot database.

Schema is still owned by Alembic migrations under `migrations/versions/`;
these classes mirror those migrations so we can query the same tables via
the ORM where it's ergonomic (e.g. the User table for fastapi-users) without
giving up the asyncpg path for vector / array / FTS operations that
SQLAlchemy doesn't express well.

If you add a column via migration, update the corresponding model here.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from app.db.session import Base


# ── Embedding dim used by both chunks.embedding and memories.embedding.
EMBEDDING_DIM = 768


class User(Base):
    """fastapi-users-compatible user record.

    Schema from migration 001 (`migrations/versions/001_create_users_and_audit.py`).
    `role` is our own column on top of the fastapi-users base — used by the
    Streamlit admin-only gate and the /admin/widgets CRUD endpoints.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), unique=True, index=True, nullable=False)
    hashed_password = Column(String(1024), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    role = Column(String(20), default="user", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    """Append-only audit trail. Schema from migration 001.

    Currently only `write_memory` writes are audited (see app/services/memory.py).
    `trace_id` and `details` are unused but reserved for richer events.
    """
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trace_id = Column(String(64), nullable=True)
    actor_type = Column(String(20), nullable=False)
    actor_id = Column(String(64), nullable=False)
    action = Column(String(50), nullable=False)
    target_type = Column(String(50), nullable=False)
    target_id = Column(String(64), nullable=False)
    details = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    """RAG corpus chunks (docs and issues). Schema from migration 002.

    The `embedding` column is a pgvector vector(768) with an HNSW index on
    cosine_ops. SQLAlchemy doesn't know about pgvector natively, so we use
    the `pgvector.sqlalchemy.Vector` type to round-trip Python lists.

    `metadata` is reserved in SQLAlchemy's Declarative metaclass, so the
    Python attribute is `metadata_` while the column name stays `metadata`.
    """
    __tablename__ = "chunks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    parent_id = Column(BigInteger, nullable=True)
    content_type = Column(String(20), nullable=False)  # 'docs' | 'issue'
    source_id = Column(String(50), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Memory(Base):
    """Episodic memories surfaced into the LLM context. Schema from migration 003.

    Deduped by (user_id, summary) via the unique index added in migration 004.
    """
    __tablename__ = "memories"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    conversation_id = Column(String(64), nullable=False)
    memory_type = Column(String(20), default="episodic")
    summary = Column(Text, nullable=False)
    entities = Column(JSONB, nullable=True)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WidgetConfig(Base):
    """Embeddable-widget runtime configuration. Schemas from migrations 005 + 006.

    `description` was added in 006 so end users on the demo host can pick a
    widget that fits their use case.
    """
    __tablename__ = "widget_configs"

    widget_id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    allowed_origins = Column(ARRAY(Text), nullable=False, default=list)
    theme = Column(JSONB, nullable=False, default=dict)
    enabled_tools = Column(ARRAY(Text), nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


__all__ = [
    "Base",
    "EMBEDDING_DIM",
    "User",
    "AuditLog",
    "Chunk",
    "Memory",
    "WidgetConfig",
]
