"""Top-level eval orchestrator.

Runs both the RAG and classification suites, merges them into a single
eval_report.json, and (when MinIO env vars are set) uploads the report
plus a `latest.json` pointer to the eval-reports bucket.

Designed to be CI-friendly:
  - Each suite is wrapped in try/except so one broken suite doesn't lose the
    other's results.
  - Suite outputs include status='ok'|'skipped'|'error' so diff tooling can
    decide what to enforce.
  - Exit code 0 unless the orchestration itself blew up (file IO, etc).
    Threshold enforcement is the diff tool's job — separating "did we produce
    a report" from "is the report acceptable" keeps failure modes legible.

Usage:
    python evals/run_all.py --output eval_report.json
    python evals/run_all.py --output eval_report.json --suites rag,classification
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

# Make /app (project root) findable from the script regardless of how it's
# launched. `python /app/evals/run_all.py` would otherwise only put
# /app/evals on sys.path, so `from model_server...` and `from evals.rag...`
# both fail.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return os.environ.get("GITHUB_SHA", "unknown")[:7]


async def _run_rag() -> dict:
    """Hit@5 and MRR@10 on the configured golden set."""
    try:
        # Local import: rag eval pulls in sentence-transformers which we don't
        # want as a hard dep of this orchestrator.
        from evals.rag.run import run as run_rag
        metrics = await run_rag()
        return {"status": "ok", "metrics": metrics}
    except ImportError as e:
        return {"status": "skipped", "reason": f"import error: {e}"}
    except Exception as e:
        return {
            "status": "error",
            "reason": str(e),
            "traceback": traceback.format_exc(limit=2),
        }


def _run_classification() -> dict:
    try:
        from evals.classification.run import run as run_cls
        return run_cls()
    except Exception as e:
        return {
            "status": "error",
            "reason": str(e),
            "traceback": traceback.format_exc(limit=2),
        }


def _upload_to_minio(report_path: Path) -> str | None:
    """Upload to MinIO if credentials are present. Returns the object key or None."""
    endpoint = os.environ.get("MINIO_ENDPOINT")
    if not endpoint:
        return None
    try:
        from minio import Minio
        client = Minio(
            endpoint,
            access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
            secure=False,
        )
        bucket = os.environ.get("MINIO_EVAL_BUCKET", "eval-reports")
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

        ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        sha = _git_sha()
        key = f"{ts}_{sha}.json"

        client.fput_object(bucket, key, str(report_path),
                           content_type="application/json")
        # Also update a 'latest.json' pointer that the diff tool can read.
        client.fput_object(bucket, "latest.json", str(report_path),
                           content_type="application/json")
        return f"{bucket}/{key}"
    except Exception as e:
        print(f"[upload] failed: {e}", file=sys.stderr)
        return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="eval_report.json",
                        help="Path to write the combined report")
    parser.add_argument("--suites", default="rag,classification",
                        help="Comma-separated subset of suites to run")
    args = parser.parse_args()

    requested = {s.strip() for s in args.suites.split(",") if s.strip()}

    report: dict = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "git_sha": _git_sha(),
        "suites": {},
    }

    if "rag" in requested:
        report["suites"]["rag"] = await _run_rag()
    if "classification" in requested:
        report["suites"]["classification"] = _run_classification()

    out = Path(args.output)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nReport written: {out}")
    print(json.dumps(report, indent=2))

    uploaded = _upload_to_minio(out)
    if uploaded:
        print(f"Uploaded to MinIO: {uploaded}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
