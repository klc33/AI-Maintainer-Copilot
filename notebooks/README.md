# Classifier retraining — Colab workflow

This folder is everything you need to retrain the issue classifier on a Colab
GPU and drop the new model back into the project. The current model on disk
is broken (macro_f1 ≈ 0.20, two classes at F1=0); the notebook here fixes the
root causes and should land at macro_f1 ≥ 0.55.

## What's in this folder

| File | Purpose |
|---|---|
| `train_classifier.ipynb` | The Colab notebook. Open this in Colab. |
| `_build_notebook.py` | Source-of-truth for the notebook cells. Edit this, then re-run `python notebooks/_build_notebook.py` to regenerate the `.ipynb`. |
| `README.md` | This file. |

## Which datasets to upload to Colab

When you reach **Step 3 — Upload data** in the notebook, upload **two files**
from your local `datasets/` directory:

| File on your machine | Size (current) | Required? | Used for |
|---|---|---|---|
| `datasets/train.csv`     | ~28 MB | **yes** | training set (the notebook applies class weights — **don't** upload `balanced_train.csv`) |
| `datasets/val.csv`       | ~4 MB  | **yes** | per-epoch validation + early stopping + final eval |
| `datasets/test.csv`      | ~7 MB  | optional | step 14 prints a held-out test-set score if you uploaded it |

All three CSVs have the same columns (only the first three are read):

```
id, title, body, label, closed_at, url
```

`label` is one of: `bug`, `feature`, `docs`, `question`.

**Do not** upload `balanced_train.csv` — the new notebook handles class
balance via weighted loss, so feeding it the pre-oversampled file would just
double-balance and slow training without improving quality.

## Step-by-step

1. Open <https://colab.research.google.com> → **File → Upload notebook** →
   pick `notebooks/train_classifier.ipynb` from this repo.
2. **Runtime → Change runtime type → T4 GPU** (the free tier is enough; A100
   or L4 is faster). Click **Save**.
3. **Runtime → Run all** (or step through cell by cell). At step 3 the file
   picker pops up — upload the two CSVs listed above.
4. The full run takes roughly:
   - **~3–4 min** per epoch on a T4 (16 GB) for ~5k–10k training rows
   - **~1–2 min** per epoch on an A100
   - Plus ~1 min for downloading the `distilbert` weights on first run
5. After the last cell, your browser downloads `classifier_v2.zip`.

## Dropping the new model back into the project

1. Stop the running model-server (so the bind-mount isn't being read):
   ```bash
   docker compose stop model-server
   ```
2. Unzip into the existing path:
   ```bash
   cd models/classifier/v1
   rm -rf ./*            # only delete what's inside, not the directory itself
   unzip /path/to/classifier_v2.zip
   ```
3. Restart the model-server:
   ```bash
   docker compose up -d model-server
   ```
4. Wait for the health check to go green (~30 s with the HF cache hot), then
   verify the new metrics show up:
   ```bash
   docker compose exec model-server cat /app/models/classifier/v1/model_card.json
   ```

If you want to keep both versions around: change the unzip target to
`models/classifier/v2/`, update `MODEL_PATH` in `docker-compose.yml` for the
`model-server` service to point at v2, and restart.

## What changed vs the previous trainer

The previous trainer (`model_server/train_classifier.py`) produced a model
that achieved 66% accuracy by predicting the majority class every time and
F1=0 on the other three classes. Fixes baked into this notebook:

| Symptom in old trainer | Fix in this notebook |
|---|---|
| `register_hook(lambda g: torch.nan_to_num(g, ...))` hid NaN gradients | Removed entirely — if gradients explode we want to see and fix the cause |
| Classifier head re-initialized with `std=0.01` (much smaller than expected) | Use the default Hugging Face init |
| `MAX_LEN=128` truncated most issue bodies before the model saw the useful content | `MAX_LEN=256` |
| Oversampling produced 25 k duplicated training rows | Replaced by class weights in the loss; each row is now unique |
| `lr_encoder == lr_head == 2e-5` (no actual discrimination) | Head LR = 5e-5, encoder LR = 2e-5 (5× ratio) |
| `microsoft/deberta-v3-small` has known disentangled-attention NaN issues | Default to `distilbert-base-uncased`. DeBERTa is still listed as an alternate if you want to try it again on GPU |
| 3 epochs, no early stopping | 5 epochs + early stopping on `macro_f1` patience 2 |

## Expected outcome

On the validation set with the current corpus:

- `macro_f1 ≥ 0.55` (vs 0.20 today) is a reasonable target — anything below
  that is still broken
- No per-class F1 at zero
- Train and val loss should both decrease and stay within ~10% of each other
  (if val diverges sharply, the model is overfitting — drop epochs or
  increase weight decay)

The `classification_report` printed in step 11 is the canonical
"did the new model actually learn anything" view — make sure every label
has precision/recall above 0.30 before swapping the model into production.

## Quick troubleshooting

- **`CUDA out of memory`** in step 10 — drop `per_device_train_batch_size`
  from 16 to 8, or `MAX_LEN` from 256 to 192.
- **`KeyError: 'label'`** at step 5 — your CSV has a different column name
  (e.g. `category` or `labels`). Rename to `label` before uploading.
- **Macro F1 plateaus around 0.30–0.40** — try `roberta-base` as
  `MODEL_NAME` (uses more memory, batch size may need to drop to 8), or
  increase epochs to 8 with patience=3.
- **Browser doesn't download the zip at the end** — pop-ups blocked. Run
  the cell manually, then in Colab's file panel (folder icon on the left)
  navigate to `/content/classifier_v2.zip` and right-click → Download.
