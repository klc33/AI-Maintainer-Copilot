# model_server/find_chunks.py
"""Find chunk IDs for golden RAG questions."""
import os
import asyncio
import asyncpg

async def search(query_text: str):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        """
        SELECT id, content_type, source_id, substring(text, 1, 200) AS snippet
        FROM chunks
        WHERE to_tsvector('english', text) @@ plainto_tsquery('english', $1)
        LIMIT 5
        """,
        query_text,
    )
    for r in rows:
        print(f"ID: {r['id']} | {r['content_type']} | {r['source_id']} | {r['snippet'][:100]}")
    await conn.close()

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "terraform init"
    asyncio.run(search(q))