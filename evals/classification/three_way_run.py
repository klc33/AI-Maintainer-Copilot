"""Three-way comparison on the 25-issue hand-curated golden set.

Runs DL (model-server's fine-tuned DistilBERT), ML (a fresh TF-IDF + LR
trained inline on train+val), and LLM (Groq llama-3.1-8b-instant via
batched few-shot prompts) against the same 25 hand-curated issues, then
prints per-class F1, macro-F1, and a confusion matrix for each.

Inputs expected at runtime:
  - evals/classification/golden_set.csv  (this script's golden set, 25 rows)
  - datasets/train.csv + datasets/val.csv (for inline ML training)
  - MODEL_SERVER_URL env (default http://model-server:8001) — for DL
  - GROQ_API_KEY env                                       — for LLM

Output:
  - JSON report at evals/classification/three_way_results.json
  - human-readable summary to stdout
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

import httpx
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.pipeline import FeatureUnion, Pipeline


LABELS = ["bug", "feature", "docs", "question"]
SCRIPT_DIR = Path(__file__).resolve().parent
GOLDEN_CSV = SCRIPT_DIR / "golden_set.csv"
RESULTS_JSON = SCRIPT_DIR / "three_way_results.json"

MODEL_SERVER_URL = os.environ.get("MODEL_SERVER_URL", "http://model-server:8001")


def _ensure_groq_key() -> None:
    """The model-server's FastAPI app loads GROQ_API_KEY into its uvicorn
    process at boot, but a fresh `docker compose exec` doesn't inherit that.
    Load it from Vault here so this script can run via exec without an
    extra -e flag dance."""
    if os.environ.get("GROQ_API_KEY"):
        return
    try:
        import hvac
        client = hvac.Client(
            url=os.environ.get("VAULT_ADDR", "http://vault:8200"),
            token=os.environ.get("VAULT_TOKEN", "root"),
        )
        secret = client.secrets.kv.v2.read_secret_version(
            path="shared/groq", mount_point="secret",
        )["data"]["data"].get("secret")
        if secret:
            os.environ["GROQ_API_KEY"] = secret
    except Exception as e:
        print(f"(could not load GROQ_API_KEY from Vault: {e})")


# ── Data loading + light cleanup ───────────────────────
def load_golden() -> pd.DataFrame:
    df = pd.read_csv(GOLDEN_CSV)
    assert set(df["label"]).issubset(LABELS), "golden set has unknown labels"
    return df


def build_texts(df: pd.DataFrame) -> list[str]:
    """Same shape both training pipelines use: title + ' ' + body."""
    return (df["title"].fillna("") + " " + df["body"].fillna("")).tolist()


# ── DL: HTTP to model-server /classify ─────────────────
async def _dl_async(texts: list[str]) -> tuple[list[str], float]:
    preds: list[str] = []
    start = time.time()
    async with httpx.AsyncClient(timeout=30) as client:
        for t in texts:
            r = await client.post(f"{MODEL_SERVER_URL}/classify", json={"text": t})
            data = r.json()
            preds.append(data.get("label") or "")
    avg_latency = (time.time() - start) / max(len(texts), 1)
    return preds, avg_latency


def dl_predict(texts: list[str]) -> tuple[list[str], float]:
    return asyncio.run(_dl_async(texts))


# ── ML: inline TF-IDF + LR train on train+val ──────────
def _ml_clean(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"```.*?```", "<CODE>", text, flags=re.DOTALL)
    text = re.sub(r"^(    |\t).*", "<CODE>", text, flags=re.MULTILINE)
    return text


def _ml_text_col(df: pd.DataFrame) -> pd.Series:
    txt = df["title"].fillna("") + " " + df["body"].fillna("")
    return txt.apply(_ml_clean)


def ml_predict(test_texts: list[str]) -> tuple[list[str], float, float]:
    """Train a fresh TF-IDF + LR pipeline on the full train+val, predict on
    the golden set. Returns (preds, train_seconds, avg_inference_seconds)."""
    train_df = pd.read_csv("datasets/train.csv")
    val_df = pd.read_csv("datasets/val.csv")

    X_train = pd.concat([_ml_text_col(train_df), _ml_text_col(val_df)])
    y_train = pd.concat([train_df["label"], val_df["label"]])

    word_tfidf = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), max_features=30000)
    char_tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=30000)
    pipe = Pipeline([
        ("features", FeatureUnion([("word", word_tfidf), ("char", char_tfidf)])),
        ("clf", LogisticRegression(C=1.0, class_weight="balanced", max_iter=1000, random_state=42)),
    ])

    t0 = time.time()
    pipe.fit(X_train, y_train)
    train_seconds = time.time() - t0

    # Apply the same cleanup to the test texts.
    cleaned = [_ml_clean(t) for t in test_texts]
    t1 = time.time()
    preds = list(pipe.predict(cleaned))
    avg_latency = (time.time() - t1) / max(len(cleaned), 1)
    return preds, train_seconds, avg_latency


# ── LLM: Groq llama-3.1-8b-instant ─────────────────────
def llm_predict(texts: list[str], batch_size: int = 10) -> tuple[list[str], float]:
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    preds: list[str] = []
    total_latency = 0.0

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        n = len(batch)
        system = {
            "role": "system",
            "content": (
                "You classify GitHub issues. Valid labels: bug, feature, docs, question.\n"
                f"You receive {n} issues numbered 1-{n}.\n"
                'Reply with a JSON object {"labels": [...]} — exactly one label per issue, in order. '
                "No prose, no explanations."
            ),
        }
        user = "\n\n".join(f"Issue {j+1}: {t[:1200]}" for j, t in enumerate(batch))

        t0 = time.time()
        r = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[system, {"role": "user", "content": user}],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        total_latency += time.time() - t0
        try:
            data = json.loads(r.choices[0].message.content)
            batch_preds = data.get("labels") or []
        except Exception:
            batch_preds = []
        # Pad/trim to batch length, coerce unknown labels to "" (shows as
        # off-class in the confusion matrix, which is fair).
        batch_preds = [p if p in LABELS else "" for p in (batch_preds + [""] * n)[:n]]
        preds.extend(batch_preds)

    avg_latency = total_latency / max(len(texts), 1)
    return preds, avg_latency


# ── Reporting ──────────────────────────────────────────
def _per_class_f1(y_true: Iterable[str], y_pred: Iterable[str]) -> dict[str, float]:
    f1s = f1_score(list(y_true), list(y_pred), average=None, labels=LABELS, zero_division=0)
    return {lab: float(f1s[i]) for i, lab in enumerate(LABELS)}


def report(name: str, y_true: list[str], y_pred: list[str]) -> dict:
    acc = float(accuracy_score(y_true, y_pred))
    macro = float(f1_score(y_true, y_pred, average="macro", labels=LABELS, zero_division=0))
    per_class = _per_class_f1(y_true, y_pred)
    cm = confusion_matrix(y_true, y_pred, labels=LABELS)

    print("=" * 72)
    print(name)
    print("=" * 72)
    print(classification_report(
        y_true, y_pred, labels=LABELS, digits=3, zero_division=0,
    ))
    print(f"accuracy: {acc:.3f}    macro_f1: {macro:.3f}")
    print()
    print("Confusion matrix  (rows=true, cols=pred, order=[bug, feature, docs, question]):")
    # Pretty-print with row labels
    header = "true\\pred  " + "  ".join(f"{lab:>9}" for lab in LABELS)
    print(header)
    for i, lab in enumerate(LABELS):
        row = "  ".join(f"{int(cm[i][j]):>9}" for j in range(len(LABELS)))
        print(f"{lab:9} {row}")
    print()
    return {
        "accuracy": acc,
        "macro_f1": macro,
        "per_class_f1": per_class,
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_labels": LABELS,
    }


def main() -> None:
    _ensure_groq_key()

    df = load_golden()
    y_true = df["label"].tolist()
    texts = build_texts(df)
    label_dist = {str(k): int(v) for k, v in df["label"].value_counts().items()}
    print(f"Loaded {len(df)} hand-curated issues from {GOLDEN_CSV.name}.")
    print("Label distribution:", label_dist)
    print()

    results: dict[str, dict] = {
        "golden_set_size": int(len(df)),
        "label_distribution": label_dist,
        "models": {},
    }

    # ── DL ──
    print("[1/3] DL (DistilBERT, /classify)...")
    dl_preds, dl_latency = dl_predict(texts)
    r = report("DL — fine-tuned DistilBERT (model-server /classify)", y_true, dl_preds)
    r["avg_latency_seconds"] = dl_latency
    results["models"]["dl"] = r

    # ── ML ──
    print("[2/3] ML (TF-IDF + LR, train fresh)...")
    ml_preds, ml_train, ml_latency = ml_predict(texts)
    r = report("ML — TF-IDF + Logistic Regression (trained fresh on train+val)", y_true, ml_preds)
    r["avg_inference_seconds"] = ml_latency
    r["training_seconds"] = ml_train
    results["models"]["ml"] = r

    # ── LLM ──
    if os.environ.get("GROQ_API_KEY"):
        print("[3/3] LLM (Groq llama-3.1-8b-instant)...")
        llm_preds, llm_latency = llm_predict(texts)
        r = report("LLM — Groq llama-3.1-8b-instant (batched=10, JSON response)", y_true, llm_preds)
        r["avg_latency_seconds"] = llm_latency
        results["models"]["llm"] = r
    else:
        print("[3/3] LLM skipped — GROQ_API_KEY not set.")
        results["models"]["llm"] = {"status": "skipped", "reason": "GROQ_API_KEY not in env"}

    RESULTS_JSON.write_text(json.dumps(results, indent=2))
    print(f"Results saved to {RESULTS_JSON}")


if __name__ == "__main__":
    main()
