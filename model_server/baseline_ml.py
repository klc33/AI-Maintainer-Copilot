# model_server/baseline_ml.py
"""Classical ML baseline: TF‑IDF + Logistic Regression."""
import os
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.metrics import accuracy_score, f1_score
import json
import time
import re

def load_data():
    train = pd.read_csv("datasets/train.csv")
    val = pd.read_csv("datasets/val.csv")
    test = pd.read_csv("datasets/test.csv")
    return train, val, test

def preprocess_text(df):
    def clean(text):
        text = re.sub(r"```.*?```", "<CODE>", text, flags=re.DOTALL)
        text = re.sub(r"^(    |\t).*", "<CODE>", text, flags=re.MULTILINE)
        return text

    df["text"] = df["title"].fillna("") + " " + df["body"].fillna("")
    df["text"] = df["text"].apply(clean)
    return df["text"]

def main():
    os.makedirs("datasets", exist_ok=True)
    train, val, test = load_data()

    X_train = preprocess_text(train)
    y_train = train["label"]
    X_val = preprocess_text(val)
    y_val = val["label"]
    X_test = preprocess_text(test)
    y_test = test["label"]

    word_tfidf = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), max_features=30000)
    char_tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=30000)

    union = FeatureUnion([
        ("word", word_tfidf),
        ("char", char_tfidf),
    ])

    pipeline = Pipeline([
        ("features", union),
        ("clf", LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)),
    ])

    best_score = -1
    best_C = 1.0
    start_time = time.time()
    for C in [0.1, 0.5, 1.0, 5.0, 10.0]:
        pipeline.set_params(clf__C=C)
        pipeline.fit(X_train, y_train)
        val_pred = pipeline.predict(X_val)
        macro_f1 = f1_score(y_val, val_pred, average="macro")
        print(f"C={C:.1f}  Val macro-F1: {macro_f1:.4f}")
        if macro_f1 > best_score:
            best_score = macro_f1
            best_C = C

    print(f"Best C = {best_C}")
    pipeline.set_params(clf__C=best_C)

    X_train_full = pd.concat([X_train, X_val])
    y_train_full = pd.concat([y_train, y_val])
    pipeline.fit(X_train_full, y_train_full)

    test_pred = pipeline.predict(X_test)
    test_f1_macro = f1_score(y_test, test_pred, average="macro")
    test_accuracy = accuracy_score(y_test, test_pred)
    per_class_f1 = f1_score(y_test, test_pred, average=None, labels=pipeline.classes_)
    latency = time.time() - start_time

    print("\n=== Test Set Results ===")
    print(f"Accuracy: {test_accuracy:.4f}")
    print(f"Macro F1: {test_f1_macro:.4f}")
    print("Per‑class F1:")
    for cls, f1 in zip(pipeline.classes_, per_class_f1):
        print(f"  {cls}: {f1:.4f}")
    print(f"Training+prediction latency (seconds): {latency:.1f}")

    results = {
        "model": "classical_ml",
        "accuracy": test_accuracy,
        "macro_f1": test_f1_macro,
        "per_class_f1": dict(zip(pipeline.classes_, per_class_f1.tolist())),
        "latency_seconds": latency,
        "C": best_C,
    }
    with open("datasets/ml_baseline_results.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()