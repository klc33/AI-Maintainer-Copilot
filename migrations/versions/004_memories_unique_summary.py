# migrations/versions/004_memories_unique_summary.py
"""Dedup historical memories and enforce uniqueness on (user_id, summary).

The chatbot used to persist only the user message into Redis history, so on
every subsequent turn the LLM re-saw "remember my name" with no record of
having handled it and re-fired the write_memory tool. Result: up to N copies
of the same summary per user.

This migration:
  1. Drops duplicate (user_id, summary) rows, keeping the newest one
     (newer rows are more likely to have real bge embeddings rather than
     zero-vector fallbacks from when /embed wasn't reachable).
  2. Creates a unique index on (user_id, summary) so the DB itself
     guarantees idempotency going forward.
"""
from alembic import op

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DELETE FROM memories AS m
        USING memories AS m2
        WHERE m.user_id = m2.user_id
          AND m.summary = m2.summary
          AND m.id < m2.id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_memories_user_summary ON memories (user_id, summary)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_memories_user_summary")
