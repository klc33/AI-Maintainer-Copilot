# model_server/search_chunks.py
"""Search chunks by phrase – returns IDs for golden set."""
import asyncio
import os
import sys
import asyncpg

async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python model_server/search_chunks.py 'search phrase'")
        return

    phrase = " ".join(sys.argv[1:])
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        "SELECT id, content_type, source_id, substring(text, 1, 120) AS snippet "
        "FROM chunks WHERE text ILIKE '%' || $1 || '%' LIMIT 5",
        phrase,
    )
    for r in rows:
        print(f"ID: {r['id']} | {r['content_type']} | {r['source_id']} | {r['snippet'][:80]}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())