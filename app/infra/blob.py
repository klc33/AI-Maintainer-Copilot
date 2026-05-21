# app/infra/blob.py
"""Tiny MinIO façade used by anything in `app/` or `evals/` that writes to
blob storage. Wraps the `minio` SDK with:

  * lazy singleton client (so cold-import doesn't try to dial MinIO)
  * idempotent bucket creation cached in-process
  * sync + async JSON / bytes upload helpers
  * a `prune_keep_newest` helper for the "last N conversations" retention
    spec for /conversations.

Bucket-naming convention (one per concern, makes lifecycle rules + IAM easier):
  - eval-reports           : eval_report.json from every CI / local run
  - conversation-snapshots : per-chat retrieved-chunks blobs
  - models                 : model_card.json manifests + per-version weights
  - training-plots         : loss / F1 curves / confusion matrices
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import datetime, timezone
from typing import Iterable

import structlog

logger = structlog.get_logger()

_client = None
_BUCKETS_CREATED: set[str] = set()

# Default bucket names — central so other modules don't repeat strings.
BUCKET_EVAL_REPORTS = "eval-reports"
BUCKET_CONVERSATIONS = "conversation-snapshots"
BUCKET_MODELS = "models"
BUCKET_TRAINING_PLOTS = "training-plots"


def get_minio_client():
    """Lazy singleton minio.Minio. Reads MINIO_* env vars at first use."""
    global _client
    if _client is None:
        from minio import Minio
        _client = Minio(
            os.environ.get("MINIO_ENDPOINT", "minio:9000"),
            access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
            secure=False,
        )
    return _client


def ensure_bucket(name: str) -> None:
    """Create the bucket if missing. Cached per-process so repeated callers
    don't hammer MinIO with bucket_exists() round-trips."""
    if name in _BUCKETS_CREATED:
        return
    client = get_minio_client()
    if not client.bucket_exists(name):
        client.make_bucket(name)
        logger.info("blob.bucket.created", bucket=name)
    _BUCKETS_CREATED.add(name)


def put_json(bucket: str, key: str, data: dict | list) -> None:
    """Synchronous JSON put. Use aput_json() from async code."""
    ensure_bucket(bucket)
    payload = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
    get_minio_client().put_object(
        bucket, key,
        data=io.BytesIO(payload),
        length=len(payload),
        content_type="application/json",
    )


def put_bytes(bucket: str, key: str, payload: bytes, content_type: str = "application/octet-stream") -> None:
    ensure_bucket(bucket)
    get_minio_client().put_object(
        bucket, key,
        data=io.BytesIO(payload),
        length=len(payload),
        content_type=content_type,
    )


async def aput_json(bucket: str, key: str, data: dict | list) -> None:
    """Async wrapper — runs the (sync) put in a thread so the event loop
    isn't blocked while we wait on MinIO."""
    try:
        await asyncio.to_thread(put_json, bucket, key, data)
    except Exception as e:
        # Snapshotting is best-effort. Never break a chat turn on a MinIO blip.
        logger.warning("blob.put_json.failed", bucket=bucket, key=key, error=str(e))


def utc_key_timestamp() -> str:
    """ISO-8601 UTC timestamp safe for use as part of an S3 key
    (colons replaced with hyphens)."""
    return datetime.now(timezone.utc).isoformat().replace(":", "-")


def prune_keep_newest(bucket: str, prefix_segment: str, keep: int) -> int:
    """Keep only the newest `keep` distinct sub-prefixes under `prefix_segment`,
    delete the rest. Sub-prefix is the first path component after
    `prefix_segment`.

    Concrete use: bucket='conversation-snapshots', prefix_segment='conversations/',
    keep=N — keeps the N most recently active conversation directories.

    Recency is measured by the lexicographically-greatest object key inside
    each sub-prefix (works because we name snapshot files with ISO timestamps)."""
    client = get_minio_client()
    if not client.bucket_exists(bucket):
        return 0

    # Group objects by their first path component below the prefix.
    by_subprefix: dict[str, list] = {}
    for obj in client.list_objects(bucket, prefix=prefix_segment, recursive=True):
        rest = obj.object_name[len(prefix_segment):]
        # rest = "{conversation_id}/{filename}"
        sub = rest.split("/", 1)[0]
        if not sub:
            continue
        by_subprefix.setdefault(sub, []).append(obj.object_name)

    if len(by_subprefix) <= keep:
        return 0

    # Sort sub-prefixes by their newest object name (lexicographic ~= timestamp).
    def latest_key(name: str) -> str:
        return max(by_subprefix[name])

    sorted_subs = sorted(by_subprefix.keys(), key=latest_key, reverse=True)
    to_evict = sorted_subs[keep:]
    deleted = 0
    for sub in to_evict:
        for key in by_subprefix[sub]:
            client.remove_object(bucket, key)
            deleted += 1
    return deleted


async def aprune_keep_newest(bucket: str, prefix_segment: str, keep: int) -> int:
    try:
        return await asyncio.to_thread(prune_keep_newest, bucket, prefix_segment, keep)
    except Exception as e:
        logger.warning("blob.prune.failed", bucket=bucket, prefix=prefix_segment, error=str(e))
        return 0


def list_keys(bucket: str, prefix: str = "", recursive: bool = True) -> Iterable[str]:
    """Lazy iterator over object keys under prefix."""
    client = get_minio_client()
    if not client.bucket_exists(bucket):
        return
    for obj in client.list_objects(bucket, prefix=prefix, recursive=recursive):
        yield obj.object_name
