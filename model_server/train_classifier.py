# model_server/train_classifier.py
"""Fine-tune DeBERTa-v3-small for issue classification (hashicorp/terraform).

Uses oversampled balanced training set to avoid class‑weight instability.
"""
import os
import json
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    TrainingArguments,
    Trainer,
    EarlyStoppingCallback,
    get_linear_schedule_with_warmup,
)
from sklearn.metrics import accuracy_score, f1_score

# ── Config ──────────────────────────────────────────────
MODEL_NAME = "microsoft/deberta-v3-small"
DATA_DIR = "datasets"
OUTPUT_DIR = "models/classifier/v1"
os.makedirs(OUTPUT_DIR, exist_ok=True)

LABELS = ["bug", "feature", "docs", "question"]
ID2LABEL = {i: l for i, l in enumerate(LABELS)}
LABEL2ID = {l: i for i, l in enumerate(LABELS)}

BATCH_SIZE = 8          # per‑GPU (or CPU)
GRAD_ACCUM = 2           # effective batch size = 16
EPOCHS = 3
LEARNING_RATE = 2e-5     # encoder
HEAD_LR = 2e-5           # same as encoder – safe default
WARMUP_RATIO = 0.1
MAX_LEN = 128            # avoids NaN in DeBERTa v3 disentangled attention
WEIGHT_DECAY = 0.01
EARLY_STOPPING_PATIENCE = 2

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# ── Dataset ─────────────────────────────────────────────
class IssueDataset(Dataset):
    def __init__(self, df, tokenizer, max_len=MAX_LEN):
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

# ── Metrics ─────────────────────────────────────────────
def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=1)
    acc = accuracy_score(labels, preds)
    macro_f1 = f1_score(labels, preds, average="macro")
    per_class = f1_score(labels, preds, average=None, labels=list(range(len(LABELS))))
    metrics = {"accuracy": acc, "macro_f1": macro_f1}
    for i, f1 in enumerate(per_class):
        metrics[f"f1_{ID2LABEL[i]}"] = f1
    return metrics

# ── Main ────────────────────────────────────────────────
def main():
    # 1. Load data – BALANCED training set, original validation set
    train_df = pd.read_csv(f"{DATA_DIR}/balanced_train.csv")
    val_df = pd.read_csv(f"{DATA_DIR}/val.csv")
    print(f"Train: {len(train_df)}  Val: {len(val_df)}")
    print("Train label distribution:\n", train_df["label"].value_counts())

    # 2. Tokenizer & model
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(LABELS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
        use_safetensors=True,
        ignore_mismatched_sizes=True,
    )

    # 3. Reinitialize classifier head with small weights.
    #    Default std=0.02 produces logits ~8x too large for DeBERTa v3's
    #    disentangled attention, causing NaN gradients on step 1.
    for name, param in model.named_parameters():
        if "classifier" in name:
            if param.dim() > 1:
                torch.nn.init.normal_(param.data, mean=0.0, std=0.01)
            else:
                torch.nn.init.zeros_(param.data)

    # Register gradient hook: zero out any NaN/inf gradients before the
    # optimizer writes them into the weights.
    for param in model.parameters():
        if param.requires_grad:
            param.register_hook(
                lambda g: torch.nan_to_num(g, nan=0.0, posinf=1.0, neginf=-1.0)
            )

    # 4. Datasets
    train_dataset = IssueDataset(train_df, tokenizer)
    val_dataset = IssueDataset(val_df, tokenizer)

    # 5. Discriminative learning rates (encoder vs head)
    encoder_params = []
    head_params = []
    for name, param in model.named_parameters():
        if "classifier" in name or "pooler" in name:
            head_params.append(param)
        else:
            encoder_params.append(param)

    optimizer = torch.optim.AdamW(
        [
            {"params": encoder_params, "lr": LEARNING_RATE},
            {"params": head_params, "lr": HEAD_LR},
        ],
        weight_decay=WEIGHT_DECAY,
    )

    # 6. Scheduler (built manually to avoid Trainer miscalculating steps)
    steps_per_epoch = max(1, len(train_dataset) // (BATCH_SIZE * GRAD_ACCUM))
    num_training_steps = steps_per_epoch * EPOCHS
    num_warmup_steps = int(num_training_steps * WARMUP_RATIO)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=num_warmup_steps,
        num_training_steps=num_training_steps,
    )
    print(f"Scheduler: {num_training_steps} steps, {num_warmup_steps} warmup steps")

    # 7. Training arguments – fp32, gradient clipping, label smoothing
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        logging_dir=f"{OUTPUT_DIR}/logs",
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=1,
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
        disable_tqdm=False,
        report_to=[],
        bf16=False,
        fp16=False,
        max_grad_norm=1.0,
        label_smoothing_factor=0.05,
    )

    # 8. Standard Trainer (no class weights needed – data is balanced)
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=EARLY_STOPPING_PATIENCE)],
        optimizers=(optimizer, scheduler),
    )

    # 8. Train
    trainer.train()

    # 9. Final evaluation on validation set
    eval_results = trainer.evaluate()
    print("Validation results:", eval_results)

    # 10. Save model & tokenizer
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # 11. Model card
    per_class_f1 = {}
    for lab in LABELS:
        key = f"eval_f1_{lab}"
        per_class_f1[lab] = eval_results.get(key, eval_results.get(f"f1_{lab}", None))

    card = {
        "model": MODEL_NAME,
        "fine_tuned_on": "hashicorp/terraform issues (oversampled training set)",
        "train_size": len(train_df),
        "val_size": len(val_df),
        "classes": LABELS,
        "hyperparameters": {
            "effective_batch_size": BATCH_SIZE * GRAD_ACCUM,
            "epochs": EPOCHS,
            "learning_rate_encoder": LEARNING_RATE,
            "learning_rate_head": HEAD_LR,
            "warmup_ratio": WARMUP_RATIO,
            "weight_decay": WEIGHT_DECAY,
            "label_smoothing_factor": 0.05,
            "max_grad_norm": 1.0,
            "mixed_precision": False,
            "class_balancing": "oversampling (each class matched to largest class count)",
        },
        "final_metrics": {
            "eval_accuracy": eval_results.get("eval_accuracy"),
            "eval_macro_f1": eval_results.get("eval_macro_f1"),
            "per_class_f1": per_class_f1,
        },
        "data_hash": "TODO",
        "weights_sha": "TODO",
    }
    with open(f"{OUTPUT_DIR}/model_card.json", "w") as f:
        json.dump(card, f, indent=2)
    print(f"Model card saved to {OUTPUT_DIR}/model_card.json")

    # Markdown version
    md = f"""# Classifier Model Card

- **Base model:** {MODEL_NAME}
- **Task:** Issue classification (bug/feature/docs/question)
- **Training data:** {len(train_df)} oversampled Terraform issues
- **Validation data:** {len(val_df)} issues (original, not oversampled)
- **Hyperparameters:** effective batch={BATCH_SIZE*GRAD_ACCUM}, lr_enc={LEARNING_RATE}, lr_head={HEAD_LR}, epochs={EPOCHS}
- **Validation accuracy:** {eval_results.get('eval_accuracy', 'N/A')}
- **Validation macro‑F1:** {eval_results.get('eval_macro_f1', 'N/A')}
"""
    with open("docs/model_card.md", "w", encoding="utf-8") as f:
        f.write(md)

    # ── Plots + MinIO upload ─────────────────────────────
    _generate_and_upload_plots(trainer, model, val_dataset, eval_results)
    _upload_manifest(card)


def _generate_and_upload_plots(trainer, model, val_dataset, eval_results: dict) -> None:
    """Draw the standard three training plots and upload PNGs to MinIO.

    Plots:
      1. Train + eval loss curves over steps.
      2. Per-class F1 + macro_F1 bar chart on the final validation set.
      3. Confusion matrix on the final validation set.
    """
    try:
        import io
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import torch
        from sklearn.metrics import confusion_matrix, f1_score
        from app.infra import blob
    except Exception as e:
        print(f"[plots] skipped — missing dep: {e}")
        return

    plots_dir = os.path.join(OUTPUT_DIR, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    minio_prefix = "classifier/v1"
    uploaded: list[str] = []

    def _save_and_upload(fig, filename: str) -> None:
        local = os.path.join(plots_dir, filename)
        fig.savefig(local, bbox_inches="tight", dpi=120)
        plt.close(fig)
        with open(local, "rb") as f:
            payload = f.read()
        blob.put_bytes(blob.BUCKET_TRAINING_PLOTS, f"{minio_prefix}/{filename}", payload, "image/png")
        uploaded.append(filename)

    # 1. Loss curves
    history = trainer.state.log_history
    train_steps, train_loss = [], []
    eval_steps, eval_loss = [], []
    for entry in history:
        if "loss" in entry and "step" in entry and "eval_loss" not in entry:
            train_steps.append(entry["step"])
            train_loss.append(entry["loss"])
        if "eval_loss" in entry:
            eval_steps.append(entry["step"])
            eval_loss.append(entry["eval_loss"])
    fig, ax = plt.subplots(figsize=(8, 5))
    if train_loss:
        ax.plot(train_steps, train_loss, label="train", alpha=0.8)
    if eval_loss:
        ax.plot(eval_steps, eval_loss, label="eval", marker="o")
    ax.set_xlabel("step"); ax.set_ylabel("loss"); ax.set_title("Training / Validation Loss")
    ax.legend(); ax.grid(alpha=0.3)
    _save_and_upload(fig, "loss_curves.png")

    # 2. Per-class F1 bar chart
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = []
    for lab in LABELS:
        bars.append(eval_results.get(f"eval_f1_{lab}", eval_results.get(f"f1_{lab}", 0.0)) or 0.0)
    ax.bar(LABELS + ["macro"], bars + [eval_results.get("eval_macro_f1", 0.0) or 0.0])
    ax.set_ylim(0, 1.0); ax.set_ylabel("F1"); ax.set_title("Validation F1 per class")
    ax.grid(axis="y", alpha=0.3)
    _save_and_upload(fig, "per_class_f1.png")

    # 3. Confusion matrix
    device = next(model.parameters()).device
    y_true, y_pred = [], []
    with torch.no_grad():
        for batch in torch.utils.data.DataLoader(val_dataset, batch_size=16):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            preds = model(input_ids=input_ids, attention_mask=attention_mask).logits.argmax(dim=1)
            y_true.extend(labels.cpu().numpy().tolist())
            y_pred.extend(preds.cpu().numpy().tolist())
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(LABELS))))
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(LABELS))); ax.set_yticks(range(len(LABELS)))
    ax.set_xticklabels(LABELS); ax.set_yticklabels(LABELS)
    ax.set_xlabel("predicted"); ax.set_ylabel("true"); ax.set_title("Confusion matrix")
    for i in range(len(LABELS)):
        for j in range(len(LABELS)):
            ax.text(j, i, str(cm[i][j]), ha="center", va="center",
                    color="white" if cm[i][j] > cm.max() / 2 else "black")
    fig.colorbar(im, ax=ax)
    _save_and_upload(fig, "confusion_matrix.png")

    print(f"[plots] uploaded to MinIO bucket={blob.BUCKET_TRAINING_PLOTS} prefix={minio_prefix}: {uploaded}")


def _upload_manifest(card: dict) -> None:
    """Upload the model_card.json manifest to MinIO. Weights stay on disk
    (the bind-mount in compose serves them at runtime); manifest in blob
    storage is the authoritative record of *what version was trained when*."""
    try:
        from app.infra import blob
    except Exception as e:
        print(f"[manifest] skipped — missing dep: {e}")
        return
    key = "classifier/v1/model_card.json"
    blob.put_json(blob.BUCKET_MODELS, key, card)
    print(f"[manifest] uploaded to MinIO bucket={blob.BUCKET_MODELS} key={key}")


if __name__ == "__main__":
    main()