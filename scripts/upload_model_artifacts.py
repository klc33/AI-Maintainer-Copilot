"""Upload existing model artifacts to MinIO without re-training.

Use case: train_classifier.py was added with auto-upload logic, but the
already-trained `models/classifier/v1` exists from before. Run this script
to populate the `models` bucket from disk so the manifest is in blob
storage regardless of whether you've retrained yet.

Usage:
    docker compose exec api /app/.venv/bin/python /app/scripts/upload_model_artifacts.py

Reads MODEL_PATH (default /app/models/classifier/v1) and uploads:
  - model_card.json    -> models/classifier/v1/model_card.json
  - weights checksums  -> models/classifier/v1/weights_index.json
    (lists every file under MODEL_PATH with its size + sha256 — small,
     so the manifest stays in blob without uploading the actual weights)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.infra import blob  # noqa: E402

MODEL_PATH = Path(os.environ.get("MODEL_PATH", "/app/models/classifier/v1"))
MINIO_PREFIX = os.environ.get("MODEL_MINIO_PREFIX", "classifier/v1")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not MODEL_PATH.exists():
        print(f"MODEL_PATH does not exist: {MODEL_PATH}", file=sys.stderr)
        return 1

    # 1. Upload model_card.json if present
    card_path = MODEL_PATH / "model_card.json"
    if card_path.exists():
        card = json.loads(card_path.read_text())
        blob.put_json(blob.BUCKET_MODELS, f"{MINIO_PREFIX}/model_card.json", card)
        print(f"uploaded: {blob.BUCKET_MODELS}/{MINIO_PREFIX}/model_card.json")
    else:
        print(f"warning: {card_path} missing — skipping manifest upload")

    # 2. Build + upload a weights index (sha256 + size for every file)
    weights_index: list[dict] = []
    for p in MODEL_PATH.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(MODEL_PATH).as_posix()
        weights_index.append({
            "path": rel,
            "size_bytes": p.stat().st_size,
            "sha256": _sha256(p),
        })

    blob.put_json(
        blob.BUCKET_MODELS,
        f"{MINIO_PREFIX}/weights_index.json",
        {"files": weights_index, "model_path": str(MODEL_PATH)},
    )
    total_mb = sum(f["size_bytes"] for f in weights_index) / (1024 * 1024)
    print(f"uploaded: {blob.BUCKET_MODELS}/{MINIO_PREFIX}/weights_index.json "
          f"({len(weights_index)} files, {total_mb:.1f} MB tracked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
