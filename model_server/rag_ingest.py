# model_server/rag_ingest.py
"""Ingest RAG corpus: chunk, embed, and store in Postgres + pgvector."""
import os
import asyncio
import json
import pandas as pd
import asyncpg
from sentence_transformers import SentenceTransformer

# Config
EMBED_MODEL_NAME = "BAAI/bge-base-en-v1.5"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
DB_URL = os.environ.get("DATABASE_URL", "postgresql://copilot:changeme@localhost:5432/copilot")

async def create_pool():
    return await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)

def chunk_text(text, max_tokens=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += max_tokens - overlap
        if start >= len(words):
            break
    return chunks

async def insert_chunks(conn, chunks_data):
    await conn.executemany(
        """
        INSERT INTO chunks (parent_id, content_type, source_id, chunk_index, text, embedding, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::vector, $7::json)
        """,
        chunks_data
    )

async def main():
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    print("Loading embedding model...")
    model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
    model.max_seq_length = 512

    pool = await create_pool()
    async with pool.acquire() as conn:
        # 1. Issues
        print("Processing RAG held‑out issues...")
        df = pd.read_csv("datasets/rag_heldout.csv")
        for _, row in df.iterrows():
            title = row["title"]
            body = row.get("body") or ""
            full_text = f"Title: {title}\n\n{body}"
            chunks = chunk_text(full_text)
            if not chunks:
                continue
            source_id = str(row["id"])
            embeddings = model.encode(chunks, batch_size=16, normalize_embeddings=True)
            chunk_rows = []
            for i, (chunk_content, emb) in enumerate(zip(chunks, embeddings)):
                embedding_str = '[' + ','.join(map(str, emb)) + ']'
                meta_str = json.dumps({"label": row.get("label"), "closed_at": row.get("closed_at")})
                chunk_rows.append((None, "issue", source_id, i, chunk_content, embedding_str, meta_str))
            await insert_chunks(conn, chunk_rows)
            print(f"  Inserted {len(chunk_rows)} chunks for issue #{source_id}")

        # 2. Docs
        docs_dir = "docs/rag_docs"
        if os.path.isdir(docs_dir):
            print("Processing docs...")
            for filename in os.listdir(docs_dir):
                if not filename.endswith((".md", ".txt", ".rst")):
                    continue
                filepath = os.path.join(docs_dir, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                chunks = chunk_text(content)
                if not chunks:
                    continue
                embeddings = model.encode(chunks, batch_size=16, normalize_embeddings=True)
                chunk_rows = []
                for i, (chunk_content, emb) in enumerate(zip(chunks, embeddings)):
                    embedding_str = '[' + ','.join(map(str, emb)) + ']'
                    meta_str = json.dumps({"breadcrumb": filename})
                    chunk_rows.append((None, "docs", filename, i, chunk_content, embedding_str, meta_str))
                await insert_chunks(conn, chunk_rows)
                print(f"  Inserted {len(chunk_rows)} chunks for doc {filename}")

    print("RAG ingestion complete.")

if __name__ == "__main__":
    asyncio.run(main())