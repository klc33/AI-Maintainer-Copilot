# model_server/list_docs.py
"""Print all docs chunks so you can grab their IDs."""
import asyncio
import os
import asyncpg

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        "SELECT id, source_id, substring(text, 1, 120) AS snippet FROM chunks WHERE content_type = 'docs'"
    )
    if not rows:
        print("No docs chunks found. You need to re-run RAG ingestion.")
    else:
        for r in rows:
            print(f"ID: {r['id']} | {r['source_id']} | {r['snippet'][:80]}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())