"""Ingest CI fixture chunks into the chunks table.

Reads evals/fixtures/chunks.jsonl, embeds each `text` field with the same
bge-base model the production pipeline uses, and inserts the rows. Designed
to be cheap enough to run on every CI build.

DATABASE_URL is consumed in the same +asyncpg-or-not format used elsewhere.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "chunks.jsonl"


def _db_url() -> str:
    raw = os.environ["DATABASE_URL"]
    return raw.replace("+asyncpg", "")


async def main() -> None:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-base-en-v1.5")

    rows = []
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    texts = [r["text"] for r in rows]
    embeddings = model.encode(texts, normalize_embeddings=True).tolist()

    conn = await asyncpg.connect(_db_url())
    try:
        # Re-create from scratch so consecutive CI runs don't accumulate duplicates.
        await conn.execute("TRUNCATE chunks RESTART IDENTITY")

        for row, emb in zip(rows, embeddings):
            emb_str = "[" + ",".join(map(str, emb)) + "]"
            metadata = json.dumps({"source": "ci_fixture"})
            await conn.execute(
                """
                INSERT INTO chunks (id, parent_id, content_type, source_id, chunk_index, text, embedding, metadata)
                VALUES ($1, NULL, $2, $3, 0, $4, $5::vector, $6::jsonb)
                """,
                row["id"],
                row["content_type"],
                row["source_id"],
                row["text"],
                emb_str,
                metadata,
            )
        # Reset the sequence past the highest hand-chosen id so future inserts
        # (e.g. recall_memories audit) don't collide.
        await conn.execute(
            "SELECT setval(pg_get_serial_sequence('chunks', 'id'), (SELECT COALESCE(MAX(id), 1) FROM chunks))"
        )
    finally:
        await conn.close()

    print(f"Ingested {len(rows)} fixture chunks.")


if __name__ == "__main__":
    asyncio.run(main())
