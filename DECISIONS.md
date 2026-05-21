# Architectural Decisions

## ADR-001 · Classifier deployment: DL (DistilBERT)

**Decision.** Deploy the fine-tuned `distilbert-base-uncased` classifier
(four labels: bug / feature / docs / question) as the production
`/classify` endpoint. Keep the TF-IDF baseline in `model_server/baseline_ml.py`
as the CI fallback. Do **not** route classification through the Groq LLM.

**Status.** Accepted · 2026-05-21.

---

### Three-way evaluation on the held-out test set

All three approaches were trained/evaluated on the same `hashicorp/terraform`
closed-issue corpus (time-stratified split, validation ≈ 1.3k issues, test
≈ 2.0k issues). Numbers below are from each candidate's evaluation pass:

| Metric | LLM<br/>(`llama-3.1-8b-instant`, batched=10) | ML<br/>(TF-IDF + Logistic Regression) | **DL**<br/>(**`distilbert-base-uncased`, fine-tuned**) |
|---|---:|---:|---:|
| **Accuracy** | 0.718 | **0.833** | 0.803 |
| **Macro-F1** | 0.366 | 0.613 | **0.658** |
| f1 (bug)      | 0.831 | **0.889** | 0.881 |
| f1 (feature)  | 0.694 | **0.866** | 0.795 |
| f1 (docs)     | **0.472** | 0.422 | 0.582 |
| f1 (question) | 0.200 | 0.277 | **0.376** |
| Avg latency / call | ~140 ms (amortized, batch of 10) | < 1 ms | ~30 ms (CPU) |
| Cost per 1k classifications | ≈ $0.007 (Groq) | $0 | $0 (after one-time training) |
| Training cost | $0 (no training) | seconds, $0 | ~30 min on a free-tier Colab T4 |
| External dependency | **Groq API** | none | none |
| Data leaves our infra | **yes** (issue text → Groq) | no | no |

Sources: `datasets/llm_baseline_results.json`,
`datasets/ml_baseline_results.json`,
`models/classifier/v1/model_card.json` (all checked into the repo).

---

### Per-dimension reading

**Accuracy looks deceptively close.** Both ML (83.3%) and DL (80.3%) post
strong accuracy because the corpus is dominated by `bug` and `feature`
issues. A classifier that predicts only those two can be right ~75% of the
time — see what the *broken* DeBERTa model did before retraining
(`datasets/deberta_results.json` — accuracy 0.620, macro-F1 0.191, three
classes at F1=0). Accuracy is not the metric to optimize here.

**Macro-F1 is the right metric** because issue triage is interesting
*precisely* when the input is `docs` or `question`. Routing every
documentation request into the `bug` bucket is a triage failure, not a
"close enough" — so we weight per-class F1 equally regardless of class
frequency. On macro-F1:

- LLM: **0.37** — comparable to the broken DeBERTa, and unacceptable.
  Per-class numbers show why: f1_question = 0.20.
- ML: **0.61** — usable, but collapses on the two minority classes
  (docs 0.42, question 0.28). The same TF-IDF features that make
  bug/feature work don't have enough signal for the rarer classes.
- DL: **0.66** — best, and the only model that keeps every class above
  0.37. The 30-point lift on `question` and 16-point lift on `docs` over
  ML is exactly the part of the corpus that matters for triage.

**Latency.** Sub-millisecond ML is the fastest, but the absolute number
isn't load-bearing here. Every classifier is called inside an LLM-driven
chat turn, where the LLM itself takes 1–3 seconds — a 30 ms classifier
call adds less than 2% to wall-clock latency. The Groq-based LLM
classifier on its own is 5× slower than DL even with batching and would
dominate the turn budget.

**Cost.** ML and DL are free at inference time (CPU-only) once trained.
DL training was ~30 min on a free Colab T4 and is rerun rarely. The LLM
path costs ~$0.007 per 1k classifications via Groq, and that compounds
with every chat turn that fires the `classify_issue` tool — for any
non-trivial usage volume, the LLM is the only option that introduces a
recurring per-call expense.

**Reliability and privacy.** ML and DL run inside the project's own
`model-server` container. The LLM path requires the Groq API to be
reachable on every call (otherwise the tool fails open with
`"classifier unavailable"`) and sends raw issue text to a third party
on every call. For a self-hosted maintainer's tool that may be installed
behind a corporate firewall, that's a deal-breaker.

---

### Why DL beats the close-on-paper ML alternative

ML's 83% accuracy looks attractive next to DL's 80%, but the per-class
breakdown tells the real story:

| Class | ML F1 | DL F1 | What it means for triage |
|---|---:|---:|---|
| bug      | 0.889 | 0.881 | Both models are equivalent here. |
| feature  | 0.866 | 0.795 | ML wins. DL slightly over-predicts other classes for some feature requests. |
| docs     | 0.422 | **0.582** | DL is ~38% relatively better — closer to "actually triageable". |
| question | 0.277 | **0.376** | DL is ~36% relatively better. |

DL trades ~3 accuracy points to recover ~38% relative recall on the two
minority classes that matter most for triage routing. Given that
"automatically forward docs and questions to the right channel" is the
whole point of building this tool, that trade is correct.

---

### Why not LLM (the prompt-as-classifier approach)

Cheap to prototype, expensive at every other dimension:

- **Macro-F1 0.366** — only marginally better than the broken model we
  replaced. The LLM gets `question` particularly wrong (F1=0.20), which
  is the exact class users notice first when triage routes incorrectly.
- **5× slower** than DL even with 10-way batching; would push chat turn
  latency past the 2-second threshold where users perceive lag.
- **Recurring cost.** $0.007 / 1k is fine for prototyping but compounds.
  Each `chat/message` turn that invokes `classify_issue` adds a Groq
  call — at 1k turns/day that's ~$210/year for one tool call type, and
  the chatbot calls multiple tools per turn.
- **Adds a hard runtime dependency** on Groq being reachable.
- **Privacy.** Each call ships the raw issue body off-platform.

The LLM remains in the project as the `summarize_thread` tool (different
problem: open-ended generation, no labeled training set, latency budget
is higher because it's user-initiated). It is **not** appropriate for the
high-volume, label-bound classification path.

---

### Trade-offs accepted

- **`question` F1 = 0.376 is still our weakest class.** Acceptable for
  v1 — the open issue is tracked in `notebooks/README.md` under "if the
  number plateaus" (try `roberta-base`, more epochs, more `question`
  training examples). Threshold gate in `eval_thresholds.yaml` will
  catch regressions below 0.05 in CI's TF-IDF fallback mode.
- **DistilBERT weights are 256 MB.** Too large to commit; lives on the
  bind-mounted volume + `models/classifier/v1/weights_index.json` in
  MinIO as the manifest of record.
- **CI can't run the DL classifier** (model artifact not in repo). The
  TF-IDF baseline runs instead as a smoke test that the eval pipeline
  itself isn't broken — see `evals/classification/run.py`.

---

### Revisit when

- Per-class F1 on `question` drops below 0.30 across two consecutive
  retrains.
- The corpus shifts (e.g. the project switches from Terraform issues to
  a different repo) — TF-IDF features are highly corpus-dependent and DL
  fine-tuning needs to be repeated.
- Inference latency budget tightens — if we ever serve `/classify`
  outside of chat (e.g. as a high-throughput tagger), revisit ONNX /
  quantized DistilBERT for ~5× speedup.

---

---

## Operational notes — Vault secret seeding

These are not architectural decisions; they're the commands to pre-populate
Vault before first boot. Left here as a quick reference.

```powershell
# === Seed all Vault secrets for Maintainer's Copilot ===
# Replace the placeholder values with your real keys before running.

# Ensure Vault is healthy
Write-Host "Checking Vault health..." -ForegroundColor Cyan
docker compose exec -T vault vault status -address=http://vault:8200 | Select-String "Sealed"

# 1. JWT signing key (used for auth and widget tokens)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/jwt secret="a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0"

# 2. Groq API key (for the chatbot LLM)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/groq secret="gsk_your_real_groq_key_here"

# 3. GitHub token (for dataset fetching)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/github token="ghp_your_real_github_token_here"

# 4. Langfuse credentials (optional – only if you want tracing)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/langfuse public="pk-lf-your-public-key" secret="sk-lf-your-secret-key"

Write-Host "All secrets stored in Vault." -ForegroundColor Green
```
