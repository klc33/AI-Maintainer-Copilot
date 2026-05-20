# migrations/versions/002_add_chunks_table.py
"""Add chunks table with pgvector embedding column (no Python pgvector import)."""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None

def upgrade():
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create table with a raw SQL vector column (768 dimensions)
    op.execute("""
        CREATE TABLE chunks (
            id BIGSERIAL PRIMARY KEY,
            parent_id BIGINT,
            content_type VARCHAR(20) NOT NULL,
            source_id VARCHAR(50) NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding vector(768) NOT NULL,
            metadata JSONB,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # Indexes
    op.create_index('ix_chunks_source', 'chunks', ['content_type', 'source_id'])
    op.create_index('ix_chunks_content_type', 'chunks', ['content_type'])
    op.execute("CREATE INDEX ix_chunks_text_fts ON chunks USING GIN (to_tsvector('english', text))")
    op.execute("CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)")

def downgrade():
    op.drop_table('chunks')