# model_server/evaluate.py
"""Evaluate fine-tuned DeBERTa-v3-small on the held-out test set."""
import json
import time
import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from sklearn.metrics import accuracy_score, f1_score

MODEL_PATH = "models/classifier/v1"
DATA_DIR = "datasets"
BATCH_SIZE = 16

LABELS = ["bug", "feature", "docs", "question"]
ID2LABEL = {i: l for i, l in enumerate(LABELS)}
LABEL2ID = {l: i for i, l in enumerate(LABELS)}

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")


class TestDataset(Dataset):
    def __init__(self, df, tokenizer, max_len=512):
        self.texts = (df["title"].fillna("") + " " + df["body"].fillna("")).tolist()
        self.labels = [LABEL2ID[l] for l in df["label"]]
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        encoding = self.tokenizer(
            self.texts[idx],
            truncation=True,
            padding="max_length",
            max_length=self.max_len,
            return_tensors="pt",
        )
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(self.labels[idx], dtype=torch.long),
        }


def main():
    # Load model and tokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_PATH,
        num_labels=len(LABELS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    ).to(device)
    model.eval()

    # Load test data
    test_df = pd.read_csv(f"{DATA_DIR}/test.csv")
    test_dataset = TestDataset(test_df, tokenizer)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)

    # Inference
    all_preds = []
    all_labels = []
    start_time = time.time()

    with torch.no_grad():
        for batch in test_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            preds = torch.argmax(outputs.logits, dim=1)

            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())

    total_time = time.time() - start_time
    avg_latency = total_time / len(test_dataset)

    # Metrics
    y_true = [ID2LABEL[l] for l in all_labels]
    y_pred = [ID2LABEL[p] for p in all_preds]

    acc = accuracy_score(y_true, y_pred)
    macro_f1 = f1_score(y_true, y_pred, average="macro")
    per_class_f1 = f1_score(y_true, y_pred, average=None, labels=LABELS)

    print("\n=== Fine‑tuned DeBERTa Test Results ===")
    print(f"Accuracy: {acc:.4f}")
    print(f"Macro F1: {macro_f1:.4f}")
    print("Per‑class F1:")
    for cls, f1 in zip(LABELS, per_class_f1):
        print(f"  {cls}: {f1:.4f}")
    print(f"Avg latency: {avg_latency:.4f}s per issue")
    print(f"Total time: {total_time:.2f}s for {len(test_dataset)} issues")

    # Save results
    results = {
        "model": "deberta_v3_small_finetuned",
        "accuracy": acc,
        "macro_f1": macro_f1,
        "per_class_f1": {cls: float(f1) for cls, f1 in zip(LABELS, per_class_f1)},
        "avg_latency_seconds": avg_latency,
        "total_time_seconds": total_time,
        "num_test_examples": len(test_dataset),
    }
    with open(f"{DATA_DIR}/deberta_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {DATA_DIR}/deberta_results.json")


if __name__ == "__main__":
    main()