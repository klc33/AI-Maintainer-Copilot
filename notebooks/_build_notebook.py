"""Build notebooks/train_classifier.ipynb programmatically.

Source-of-truth for the cells lives below as Python strings — easier to
maintain than raw ipynb JSON. Re-run after editing:

    python notebooks/_build_notebook.py
"""
from __future__ import annotations
import json, sys
from pathlib import Path


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": [l + "\n" for l in text.splitlines()] or [""]}


def code(text: str) -> dict:
    lines = text.splitlines()
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [l + ("\n" if i < len(lines) - 1 else "") for i, l in enumerate(lines)] or [""],
    }


CELLS: list[dict] = []

# ── 0. Overview ────────────────────────────────────────
CELLS.append(md("""\
# Classifier Retraining — Maintainer's Copilot

This notebook retrains the issue classifier (bug / feature / docs / question)
with the fixes needed to escape the current `macro_f1 ≈ 0.20` collapse.

**Why the previous trainer collapsed:**

The on-disk `model_card.json` reports `eval_accuracy=0.66, eval_macro_f1=0.20`.
That spread is the textbook signature of the model predicting only the
majority class (`bug` after oversampling) — high accuracy on that one class,
F1=0 on the other three. Root causes in the previous trainer:

1. A `param.register_hook(... torch.nan_to_num(...))` zeroed any NaN/inf
   gradient before the optimizer step. That **hides** the real numerical
   instability rather than fixing it — the model learned only the class prior.
2. The classifier head was re-initialized with `std=0.01`, much smaller than
   the optimizer expected. Combined with the gradient hook, learning signal
   to the head was effectively zero.
3. `MAX_LEN=128` truncates most issue bodies before the model sees the
   informative middle / end.
4. Oversampling + balanced classes meant the model could memorize a few
   examples without learning class boundaries.

**What this notebook changes:**

- `distilbert-base-uncased` (stable, fast, 67M params) — avoids DeBERTa's
  disentangled-attention NaN issues entirely. Keep deberta-v3-small as a
  comment alternative if you want to try it again on GPU.
- `MAX_LEN=256` — enough for most issue titles + bodies.
- **Class weights via `compute_class_weight('balanced')`** instead of
  oversampling. Same effect on the loss, but no fake-duplicated rows and
  the model sees more diverse examples per epoch.
- No NaN-zeroing hook. If gradients explode, we want to *see* them so we
  can fix the cause, not paper over them.
- No tiny head re-init. The pretrained head shape is fine; default init.
- Discriminative LRs: head=5e-5, encoder=2e-5 (5× ratio is standard).
- FP16 mixed precision on GPU. Faster, no quality loss for this size.
- Early stopping on `eval_macro_f1`, patience=2.
- Per-class F1 reported every epoch; final `classification_report` and
  confusion matrix make per-class behavior visible.

**Expected outcome:** `macro_f1 ≥ 0.55` on the validation set (vs 0.20 today),
with no class at F1=0.
"""))

# ── 1. Install ─────────────────────────────────────────
CELLS.append(md("""## 1. Install dependencies"""))
CELLS.append(code("""\
# Colab usually already has torch + transformers, but we pin the versions
# that match the production model_server image.
!pip install -q "transformers>=4.46,<5" "accelerate>=1" datasets pandas scikit-learn matplotlib seaborn"""))

# ── 2. GPU check ───────────────────────────────────────
CELLS.append(md("""## 2. Confirm a GPU is attached"""))
CELLS.append(code("""\
import torch
print('CUDA available :', torch.cuda.is_available())
if torch.cuda.is_available():
    print('Device         :', torch.cuda.get_device_name(0))
    print('Memory total   :', round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1), 'GB')
else:
    print('⚠️  Running on CPU — expect ~hours to train. Runtime → Change runtime type → T4 GPU.')"""))

# ── 3. Upload data ─────────────────────────────────────
CELLS.append(md("""## 3. Upload data

When the file picker pops up, upload **`train.csv`** and **`val.csv`** from
your local `datasets/` directory. Optionally also upload `test.csv` if you
want a final test-set score at the end.

Required columns: `title`, `body`, `label`. Label values: `bug`, `feature`, `docs`, `question`."""))
CELLS.append(code("""\
from google.colab import files
uploaded = files.upload()
print('Uploaded:', list(uploaded.keys()))"""))

# ── 4. Load + EDA ──────────────────────────────────────
CELLS.append(md("""## 4. Load and inspect the data"""))
CELLS.append(code("""\
import os, json, re, random
import numpy as np
import pandas as pd

train_df = pd.read_csv('train.csv')
val_df   = pd.read_csv('val.csv')
print(f'Train rows: {len(train_df):>5}')
print(f'Val   rows: {len(val_df):>5}')
print()
print('Train label distribution:')
print(train_df['label'].value_counts())
print()
print('Val label distribution:')
print(val_df['label'].value_counts())"""))

# ── 5. Preprocess + tokenize ───────────────────────────
CELLS.append(md("""## 5. Light text cleanup + tokenization

We strip code fences, inline code, HTML tags, and URLs (replacing each with
a placeholder token). This keeps useful natural-language context without
letting the model pattern-match on URL slugs or stack-trace minutiae."""))
CELLS.append(code("""\
LABELS = ['bug', 'feature', 'docs', 'question']
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for i, l in enumerate(LABELS)}


def clean(text):
    if not isinstance(text, str):
        return ''
    text = re.sub(r'```.*?```', ' <CODE> ', text, flags=re.DOTALL)
    text = re.sub(r'`[^`\\n]{1,100}`', ' <CODE> ', text)
    text = re.sub(r'<[^>]{1,100}>', ' ', text)
    text = re.sub(r'https?://\\S+', ' <URL> ', text)
    text = re.sub(r'\\s+', ' ', text)
    return text.strip()


def build_text(row):
    return (row['title'] or '') + ' ' + clean(row['body'] or '')


train_df['text']     = train_df.apply(build_text, axis=1)
val_df['text']       = val_df.apply(build_text, axis=1)
train_df['label_id'] = train_df['label'].map(LABEL2ID)
val_df['label_id']   = val_df['label'].map(LABEL2ID)
print('Median train text length (chars):', int(train_df['text'].str.len().median()))
print('p95 train text length (chars):', int(train_df['text'].str.len().quantile(0.95)))"""))

# ── 6. Tokenizer + datasets ────────────────────────────
CELLS.append(md("""## 6. Tokenizer + PyTorch datasets

`MODEL_NAME` is the only knob you'd typically touch here. distilbert is fast
and stable; if you want to push the ceiling, try `roberta-base` (110M params)
or `microsoft/deberta-v3-base` (140M)."""))
CELLS.append(code("""\
from torch.utils.data import Dataset
from transformers import AutoTokenizer

MODEL_NAME = 'distilbert-base-uncased'  # alternates: 'roberta-base', 'microsoft/deberta-v3-base'
MAX_LEN    = 256

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)


class IssueDataset(Dataset):
    def __init__(self, df, tokenizer, max_len=MAX_LEN):
        self.texts  = df['text'].tolist()
        self.labels = df['label_id'].tolist()
        self.tok    = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, i):
        enc = self.tok(self.texts[i], truncation=True, padding='max_length',
                       max_length=self.max_len, return_tensors='pt')
        return {
            'input_ids':      enc['input_ids'].squeeze(0),
            'attention_mask': enc['attention_mask'].squeeze(0),
            'labels':         torch.tensor(self.labels[i], dtype=torch.long),
        }


train_ds = IssueDataset(train_df, tokenizer)
val_ds   = IssueDataset(val_df,   tokenizer)
print(f'tokenized: train={len(train_ds)} val={len(val_ds)}')"""))

# ── 7. Class weights ───────────────────────────────────
CELLS.append(md("""## 7. Class weights (replaces oversampling)

`sklearn.utils.class_weight.compute_class_weight('balanced')` returns
weights that inversely scale with class frequency. Multiplied into the
CrossEntropyLoss, each class contributes equally regardless of how many
examples it has."""))
CELLS.append(code("""\
from sklearn.utils.class_weight import compute_class_weight

class_weights_np = compute_class_weight(
    'balanced',
    classes=np.arange(len(LABELS)),
    y=train_df['label_id'].values,
)
class_weights = torch.tensor(class_weights_np, dtype=torch.float32)
print('Class weights:')
for lab, w in zip(LABELS, class_weights.tolist()):
    print(f'  {lab:9}: {w:.3f}')"""))

# ── 8. Trainer + model + metrics ───────────────────────
CELLS.append(md("""## 8. Model + weighted Trainer + metrics

`WeightedTrainer` overrides `compute_loss` to apply our class weights with
a small label-smoothing term. Everything else is stock Hugging Face Trainer."""))
CELLS.append(code("""\
from transformers import AutoModelForSequenceClassification, Trainer, TrainingArguments, EarlyStoppingCallback
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix

model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=len(LABELS),
    id2label=ID2LABEL,
    label2id=LABEL2ID,
)


class WeightedTrainer(Trainer):
    \"\"\"Cross-entropy with class weights + light label smoothing.\"\"\"
    def __init__(self, *args, class_weights=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop('labels')
        outputs = model(**inputs)
        loss_fct = torch.nn.CrossEntropyLoss(
            weight=self.class_weights.to(model.device),
            label_smoothing=0.05,
        )
        loss = loss_fct(outputs.logits, labels)
        return (loss, outputs) if return_outputs else loss


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=1)
    out = {
        'accuracy': float(accuracy_score(labels, preds)),
        'macro_f1': float(f1_score(labels, preds, average='macro', labels=list(range(len(LABELS))), zero_division=0)),
    }
    per_class = f1_score(labels, preds, average=None, labels=list(range(len(LABELS))), zero_division=0)
    for i, val in enumerate(per_class):
        out[f'f1_{ID2LABEL[i]}'] = float(val)
    return out"""))

# ── 9. Training args ───────────────────────────────────
CELLS.append(md("""## 9. Training arguments

- **5 epochs** with early stopping (patience 2) on `eval_macro_f1`
- Effective batch size **16** (train), **32** (eval)
- Discriminative LRs are set inside the Trainer via the param-group split below
- `fp16` is auto-enabled when a GPU is present"""))
CELLS.append(code("""\
# Discriminative LRs: head moves 2.5× faster than encoder.
HEAD_LR    = 5e-5
ENCODER_LR = 2e-5

# Split parameters into two groups by name (head/pooler vs everything else).
head_params, encoder_params = [], []
for name, p in model.named_parameters():
    if not p.requires_grad:
        continue
    if 'classifier' in name or 'pre_classifier' in name or 'pooler' in name:
        head_params.append(p)
    else:
        encoder_params.append(p)

optimizer = torch.optim.AdamW(
    [
        {'params': encoder_params, 'lr': ENCODER_LR},
        {'params': head_params,    'lr': HEAD_LR},
    ],
    weight_decay=0.01,
)

training_args = TrainingArguments(
    output_dir='classifier_v2',
    num_train_epochs=5,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,
    gradient_accumulation_steps=1,
    warmup_ratio=0.1,
    weight_decay=0.01,
    logging_steps=20,
    eval_strategy='epoch',
    save_strategy='epoch',
    save_total_limit=1,
    load_best_model_at_end=True,
    metric_for_best_model='macro_f1',
    greater_is_better=True,
    fp16=torch.cuda.is_available(),
    bf16=False,
    max_grad_norm=1.0,
    report_to=[],
    disable_tqdm=False,
)

trainer = WeightedTrainer(
    model=model,
    args=training_args,
    train_dataset=train_ds,
    eval_dataset=val_ds,
    compute_metrics=compute_metrics,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    class_weights=class_weights,
    optimizers=(optimizer, None),  # use our optimizer; let Trainer build the scheduler
)"""))

# ── 10. Train ──────────────────────────────────────────
CELLS.append(md("""## 10. Train"""))
CELLS.append(code("""\
trainer.train()"""))

# ── 11. Evaluate ───────────────────────────────────────
CELLS.append(md("""## 11. Final evaluation on validation set"""))
CELLS.append(code("""\
eval_results = trainer.evaluate()
print('=== Final validation metrics ===')
for k, v in eval_results.items():
    if isinstance(v, (int, float)):
        print(f'  {k:25}: {v:.4f}')

# Per-class report + confusion matrix
val_preds = trainer.predict(val_ds)
y_true = val_preds.label_ids
y_pred = np.argmax(val_preds.predictions, axis=1)
print()
print(classification_report(
    y_true, y_pred,
    labels=list(range(len(LABELS))),
    target_names=LABELS,
    digits=3,
    zero_division=0,
))"""))

# ── 12. Plots ──────────────────────────────────────────
CELLS.append(md("""## 12. Plots: loss curves, val macro_f1, confusion matrix"""))
CELLS.append(code("""\
import matplotlib.pyplot as plt
import seaborn as sns

history = pd.DataFrame(trainer.state.log_history)

fig, axes = plt.subplots(1, 2, figsize=(13, 4))

# Loss
if 'loss' in history.columns:
    tl = history.dropna(subset=['loss'])
    axes[0].plot(tl['step'], tl['loss'], label='train', alpha=0.85)
if 'eval_loss' in history.columns:
    el = history.dropna(subset=['eval_loss'])
    axes[0].plot(el['step'], el['eval_loss'], label='val', marker='o')
axes[0].set_xlabel('step'); axes[0].set_ylabel('loss')
axes[0].set_title('Training / Validation loss')
axes[0].legend(); axes[0].grid(alpha=0.3)

# macro_f1
if 'eval_macro_f1' in history.columns:
    mf = history.dropna(subset=['eval_macro_f1'])
    axes[1].plot(mf['step'], mf['eval_macro_f1'], marker='o', color='tab:green')
axes[1].set_xlabel('step'); axes[1].set_ylabel('macro F1'); axes[1].set_ylim(0, 1)
axes[1].set_title('Validation macro F1'); axes[1].grid(alpha=0.3)

plt.tight_layout()
plt.savefig('classifier_v2/loss_curves.png', dpi=120, bbox_inches='tight')
plt.show()

# Confusion matrix
cm = confusion_matrix(y_true, y_pred, labels=list(range(len(LABELS))))
fig, ax = plt.subplots(figsize=(6, 5))
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
            xticklabels=LABELS, yticklabels=LABELS, ax=ax)
ax.set_xlabel('predicted'); ax.set_ylabel('true')
ax.set_title('Confusion matrix (val)')
plt.tight_layout()
plt.savefig('classifier_v2/confusion_matrix.png', dpi=120, bbox_inches='tight')
plt.show()"""))

# ── 13. Save + model_card + download ───────────────────
CELLS.append(md("""## 13. Save model + model_card + download zip

After this cell runs, your browser downloads `classifier_v2.zip`. Unzip it
into your local project at `models/classifier/v1/` (replacing the existing
contents) — the model-server picks it up on next restart.

The zip contains: `model.safetensors`, `config.json`, `tokenizer.json`,
`tokenizer_config.json`, `vocab.txt` (or equivalent), `model_card.json`,
plus the two PNG plots."""))
CELLS.append(code("""\
import json

trainer.save_model('classifier_v2')
tokenizer.save_pretrained('classifier_v2')

card = {
    'model':            MODEL_NAME,
    'fine_tuned_on':    'hashicorp/terraform issues',
    'train_size':       int(len(train_df)),
    'val_size':         int(len(val_df)),
    'classes':          LABELS,
    'hyperparameters': {
        'max_len':            MAX_LEN,
        'batch_size':         16,
        'epochs':             5,
        'lr_encoder':         ENCODER_LR,
        'lr_head':            HEAD_LR,
        'warmup_ratio':       0.1,
        'weight_decay':       0.01,
        'label_smoothing':    0.05,
        'class_balancing':    'class weights (sklearn balanced)',
        'fp16':               bool(torch.cuda.is_available()),
        'max_grad_norm':      1.0,
        'early_stopping':     2,
    },
    'final_metrics': {k: v for k, v in eval_results.items() if isinstance(v, (int, float))},
}
with open('classifier_v2/model_card.json', 'w') as f:
    json.dump(card, f, indent=2)
print(json.dumps(card, indent=2))

# Zip + download
!cd classifier_v2 && zip -rq /content/classifier_v2.zip .

from google.colab import files
files.download('/content/classifier_v2.zip')"""))

# ── 14. (Optional) test set ────────────────────────────
CELLS.append(md("""## 14. (Optional) score on `test.csv`

If you uploaded `test.csv` in step 3, run this cell for the final
"production" number. The test set was held out during training and
hyperparameter selection, so it's the cleanest read on real-world quality."""))
CELLS.append(code("""\
if os.path.exists('test.csv'):
    test_df = pd.read_csv('test.csv')
    test_df['text']     = test_df.apply(build_text, axis=1)
    test_df['label_id'] = test_df['label'].map(LABEL2ID)
    test_ds = IssueDataset(test_df, tokenizer)
    test_preds = trainer.predict(test_ds)
    y_true_t = test_preds.label_ids
    y_pred_t = np.argmax(test_preds.predictions, axis=1)
    print('=== Test set classification report ===')
    print(classification_report(
        y_true_t, y_pred_t,
        labels=list(range(len(LABELS))),
        target_names=LABELS,
        digits=3,
        zero_division=0,
    ))
else:
    print('test.csv not uploaded — skipping test-set evaluation.')"""))

NOTEBOOK = {
    "cells": CELLS,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
        "colab": {"name": "train_classifier", "provenance": []},
        "accelerator": "GPU",
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out_path = Path(__file__).parent / "train_classifier.ipynb"
out_path.write_text(json.dumps(NOTEBOOK, indent=1), encoding="utf-8")
print(f"wrote {out_path}  ({len(CELLS)} cells)")
