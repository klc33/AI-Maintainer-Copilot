# migrations/versions/003_add_memories_table.py
"""Add episodic memory table (no pgvector import)."""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None

def upgrade():
    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE memories (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL,
            conversation_id VARCHAR(64) NOT NULL,
            memory_type VARCHAR(20) DEFAULT 'episodic',
            summary TEXT NOT NULL,
            entities JSONB,
            embedding vector(768) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.create_index('ix_memories_user', 'memories', ['user_id', 'created_at'])
    op.execute("CREATE INDEX ix_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)")

def downgrade():
    op.drop_table('memories')