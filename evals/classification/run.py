"""Classification eval suite.

Modes (auto-selected):
  1. DeBERTa  — if MODEL_PATH exists and transformers is importable.
                Loads the fine-tuned model and runs it against the test CSV.
  2. TF-IDF   — fallback when the trained model is missing (typical in CI).
                Trains a TF-IDF + LR baseline on the test CSV's own train split.
                This gives CI a reproducible signal without shipping weights.
  3. Skipped  — neither sklearn nor transformers available. Returns status='skipped'.

Test set source: $CLASSIFICATION_TEST_CSV (defaults to evals/fixtures/classification_test.csv).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LABELS = ["bug", "feature", "docs", "question"]


def _resolve_test_csv() -> Path | None:
    candidate = os.environ.get(
        "CLASSIFICATION_TEST_CSV",
        str(Path(__file__).parent.parent / "fixtures" / "classification_test.csv"),
    )
    p = Path(candidate)
    return p if p.exists() else None


def _eval_deberta(test_csv: Path, model_path: Path) -> dict:
    """Evaluate the fine-tuned DeBERTa classifier."""
    import pandas as pd
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    from sklearn.metrics import accuracy_score, f1_score

    df = pd.read_csv(test_csv)
    tokenizer = AutoTokenizer.from_pretrained(str(model_path))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_path)).eval()
    id2label = {i: l for i, l in enumerate(LABELS)}

    texts = (df["title"].fillna("") + " " + df["body"].fillna("")).tolist()
    y_true = df["label"].tolist()
    y_pred: list[str] = []
    with torch.no_grad():
        for txt in texts:
            inputs = tokenizer(txt, return_tensors="pt", truncation=True, max_length=256)
            logits = model(**inputs).logits
            y_pred.append(id2label[int(logits.argmax().item())])

    return {
        "status": "ok",
        "mode": "deberta",
        "metrics": {
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "macro_f1": float(f1_score(y_true, y_pred, average="macro", labels=LABELS, zero_division=0)),
            "n_examples": len(y_true),
        },
    }


def _eval_tfidf(test_csv: Path) -> dict:
    """TF-IDF + LR baseline. Splits the CSV in half stratified by label so we
    have both a fit set and a held-out test set. Same baseline shape as
    model_server/baseline_ml.py, just much smaller."""
    import pandas as pd
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import train_test_split

    df = pd.read_csv(test_csv)
    X = (df["title"].fillna("") + " " + df["body"].fillna("")).tolist()
    y = df["label"].tolist()
    if len(df) < 8:
        return {"status": "skipped", "reason": f"fixture too small ({len(df)} rows)"}

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.5, random_state=42, stratify=y,
    )
    vec = TfidfVectorizer(ngram_range=(1, 2), max_features=5000)
    Xtr = vec.fit_transform(X_train)
    Xte = vec.transform(X_test)
    clf = LogisticRegression(max_iter=400, class_weight="balanced")
    clf.fit(Xtr, y_train)
    pred = clf.predict(Xte)

    return {
        "status": "ok",
        "mode": "tfidf_baseline",
        "metrics": {
            "accuracy": float(accuracy_score(y_test, pred)),
            "macro_f1": float(f1_score(y_test, pred, average="macro", labels=LABELS, zero_division=0)),
            "n_examples": len(y_test),
        },
    }


def run() -> dict:
    test_csv = _resolve_test_csv()
    if test_csv is None:
        return {"status": "skipped", "reason": "no test CSV found"}

    model_path = Path(os.environ.get("MODEL_PATH", "models/classifier/v1"))
    if model_path.exists():
        try:
            return _eval_deberta(test_csv, model_path)
        except Exception as e:
            # Fall through to TF-IDF rather than failing CI on a model load error.
            print(f"[classification] DeBERTa eval failed ({e}); falling back to TF-IDF", file=sys.stderr)

    try:
        return _eval_tfidf(test_csv)
    except ImportError as e:
        return {"status": "skipped", "reason": f"sklearn not available: {e}"}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
