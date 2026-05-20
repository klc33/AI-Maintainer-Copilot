# model_server/data.py
"""Fetch **all** Terraform closed issues with target labels using sliding time windows.

Uses the standard Issues API (not Search), which has a 5,000‑request/hour limit and
no per‑query result cap.  We slide the `since` parameter forward after each batch,
guaranteeing we never hit the 5,000‑item‑per‑response limit.
"""

import os
import time
from datetime import datetime, timezone
from io import BytesIO

import requests
import pandas as pd
from minio import Minio

OWNER = "hashicorp"
REPO = "terraform"
# Start fetching issues updated on or after this date
SINCE = "2020-01-01T00:00:00Z"
PER_PAGE = 100
MAX_RETRIES = 3

# Label mapping
LABEL_MAP = {
    "bug": "bug",
    "enhancement": "feature",
    "documentation": "docs",
    "question": "question",
}
PRIORITY = ["bug", "feature", "docs", "question"]

# MinIO config
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
BUCKET_NAME = "datasets"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

def fetch_all_issues() -> list[dict]:
    """
    Fetch all closed issues (no PRs) updated since `SINCE` using cursor‑based
    pagination via the Link header.  Works with any number of issues.
    """
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    base_url = f"https://api.github.com/repos/{OWNER}/{REPO}/issues"
    params = {
        "state": "closed",
        "since": SINCE,
        "per_page": 100,
        "direction": "asc",
        "sort": "updated",
    }

    all_issues = []

    while True:
        for attempt in range(MAX_RETRIES):
            resp = requests.get(base_url, headers=headers, params=params)
            if resp.status_code == 200:
                break
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                wait = int(resp.headers.get("Retry-After", 60))
                print(f"Rate limit hit, waiting {wait}s ...")
                time.sleep(wait)
                continue
            print(f"Error {resp.status_code}: {resp.text}")
            break
        else:
            print(f"Failed after {MAX_RETRIES} retries. Stopping.")
            break

        issues = resp.json()
        if not isinstance(issues, list):
            # Safety net – in case of a 422 or unexpected format
            print(f"Unexpected response: {issues}")
            break

        # Filter out pull requests
        for issue in issues:
            if "pull_request" in issue:
                continue
            all_issues.append(issue)

        print(f"Page: {len(issues)} issues (total {len(all_issues)})")

        # ── Cursor‑based pagination via Link header ──
        link_header = resp.headers.get("Link")
        next_url = None
        if link_header:
            links = requests.utils.parse_header_links(link_header)
            for link in links:
                if link.get("rel") == "next":
                    next_url = link["url"]
                    break

        if not next_url:
            # No more pages
            break

        # Extract parameters from the next‑page URL and use them
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(next_url)
        new_params = parse_qs(parsed.query)
        # Convert list values back to strings
        params = {k: v[0] if isinstance(v, list) else v for k, v in new_params.items()}

        time.sleep(0.5)   # be gentle

    return all_issues


def map_label(labels: list[dict]) -> str | None:
    matched = []
    for label in labels:
        name = label["name"].lower().strip()
        if name in LABEL_MAP:
            matched.append(LABEL_MAP[name])
    if not matched:
        return None
    for p in PRIORITY:
        if p in matched:
            return p
    return None

def preprocess_issues(raw_issues: list[dict]) -> pd.DataFrame:
    rows = []
    for issue in raw_issues:
        label = map_label(issue.get("labels", []))
        if label is None:
            continue
        rows.append({
            "id": issue["number"],
            "title": issue["title"],
            "body": issue.get("body") or "",
            "label": label,
            "closed_at": issue.get("closed_at"),
            "url": issue.get("html_url"),
        })
    if not rows:
        return pd.DataFrame(columns=["id", "title", "body", "label", "closed_at", "url"])
    df = pd.DataFrame(rows)
    df["closed_at"] = pd.to_datetime(df["closed_at"])
    df = df.sort_values("closed_at").reset_index(drop=True)
    return df

def save_to_minio(df: pd.DataFrame, object_name: str):
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    try:
        if not client.bucket_exists(BUCKET_NAME):
            client.make_bucket(BUCKET_NAME)
    except Exception as e:
        print(f"MinIO bucket check failed: {e}")
        return
    parquet_bytes = df.to_parquet(index=False)
    client.put_object(
        BUCKET_NAME,
        object_name,
        data=BytesIO(parquet_bytes),
        length=len(parquet_bytes),
        content_type="application/octet-stream",
    )
    print(f"Saved {object_name} to MinIO bucket {BUCKET_NAME}")

def main():
    print("Fetching ALL closed issues from hashicorp/terraform (sliding window) …")
    raw = fetch_all_issues()
    print(f"Total raw issues fetched: {len(raw)}")

    if not raw:
        print("No issues fetched – check token / network.")
        return

    df = preprocess_issues(raw)
    print(f"After filtering: {len(df)} issues")
    print("Label distribution:")
    print(df["label"].value_counts())

    os.makedirs("datasets", exist_ok=True)
    csv_path = "datasets/terraform_issues.csv"
    df.to_csv(csv_path, index=False)
    print(f"Saved locally to {csv_path}")

    save_to_minio(df, "terraform_issues.parquet")

if __name__ == "__main__":
    if not GITHUB_TOKEN:
        print("Please set GITHUB_TOKEN environment variable")
        exit(1)
    main()