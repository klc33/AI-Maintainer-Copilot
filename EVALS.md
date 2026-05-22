# Evals

How the Maintainer's Copilot is measured, and how a regression blocks a merge.
Two suites — **RAG** and **classification** — run on **every push and PR** via
the `Evals` GitHub Actions workflow ([.github/workflows/eval.yml](.github/workflows/eval.yml)).
See [RUNBOOK.md](RUNBOOK.md) → "CI eval regression" for triaging a red run.

---

## TL;DR

| Suite | Metrics | Gated in CI |
|---|---|---|
| RAG — retrieval | Hit@5, MRR@10 | yes |
| RAG — generation | faithfulness, answer_relevancy, answer_correctness | faithfulness + correctness |
| RAG — judge trust | judge↔human agreement, MAE | agreement |
| Classification | accuracy, macro-F1 | yes |

Every metric is written to `eval_report.json`, uploaded as a workflow
artifact, and diffed against the last green run on `main` by
[evals/diff.py](evals/diff.py) against the floors in
[eval_thresholds.yaml](eval_thresholds.yaml).

---

## RAG eval

### The golden set — 25 triples

[evals/rag/golden.jsonl](evals/rag/golden.jsonl) — 25 lines, each a
`question` / `ideal_answer` / `chunk_ids` triple:

```json
{"question": "What does terraform init do?",
 "ideal_answer": "terraform init initializes a working directory ...",
 "chunk_ids": [888]}
```

`chunk_ids` are the ground-truth chunks in the production corpus; `ideal_answer`
is the reference answer the generated answer is graded against. CI runs against
the 6-question fixture set ([evals/fixtures/golden.jsonl](evals/fixtures/golden.jsonl),
corpus = [evals/fixtures/chunks.jsonl](evals/fixtures/chunks.jsonl)) so it needs
no production data — switch with `$RAG_GOLDEN_PATH`.

### Retrieval metrics

Computed in [evals/rag/run.py](evals/rag/run.py) by running the full pipeline
(`model_server.rag_retrieval.retrieve` — dense + sparse + weighted RRF +
cross-encoder rerank) for each question:

- **Hit@5** — fraction of questions where a ground-truth chunk is in the top 5.
- **MRR@10** — mean reciprocal rank of the first ground-truth chunk in the top 10.

### Generation metrics

For each golden question we generate an answer from its **top-5 retrieved
chunks** ([evals/rag/generation.py](evals/rag/generation.py), `generate_answer`,
using the same `llama-3.1-8b-instant` the app ships) and score it on three axes,
each `0.0–1.0`:

- **faithfulness** — is every claim grounded in the retrieved context? (anti-hallucination)
- **answer_relevancy** — does the answer address the question?
- **answer_correctness** — does the answer agree with `ideal_answer`?

### The judge — a frozen judge model, not RAGAS

The brief allowed RAGAS *or* a frozen judge. We chose a **frozen judge model**
([evals/rag/judge.py](evals/rag/judge.py)):

- **Frozen** = a pinned model (`llama-3.3-70b-versatile`), deterministic
  decoding (`temperature=0`, fixed `seed`), and a **versioned rubric**
  (`JUDGE_PROMPT_VERSION`). Bump the version whenever the rubric changes so
  past reports stay comparable.
- A 70B model judges the 8B generator's output — the judge is stronger than
  the thing it grades.
- Scores are elicited on a 0–4 integer scale (LLMs are far more consistent on
  a short integer scale) and normalised to `{0, .25, .5, .75, 1}`.
- **Why not RAGAS** — RAGAS is itself an LLM call behind a fast-moving 0.x API;
  pinning *that* is harder than pinning our own one-call judge. More
  importantly, a hand-written rubric is **auditable and calibratable** — which
  is the whole point of the next section. (`ragas` is still a declared dep in
  `pyproject.toml`; nothing stops a future RAGAS-based suite alongside this.)

### Judge calibration — 5 hand-labelled answers

A judge you can't check is just one model's opinion. So 5 of the 25 questions
have a **hand-labelled candidate answer** in
[evals/rag/human_labels.jsonl](evals/rag/human_labels.jsonl) — labelled by
`klc33` on the same `{0, .25, .5, .75, 1}` buckets the judge uses. The set is
deliberately spread across answer qualities:

| id | candidate answer is… | faithfulness | relevancy | correctness |
|---|---|---|---|---|
| `init-good` | fully correct, fully grounded | 1.00 | 1.00 | 1.00 |
| `state-locking-wrong-mechanism` | invented `lock = true` mechanism | 0.25 | 0.75 | 0.25 |
| `provider-correct-plus-hallucination` | correct + one fabricated claim | 0.50 | 1.00 | 0.75 |
| `destroy-answers-wrong-question` | true, but answers a different question | 0.50 | 0.00 | 0.00 |
| `validate-good` | fully correct, fully grounded | 1.00 | 1.00 | 1.00 |

Every run, the judge scores these same 5 fixed answers and
[evals/rag/generation.py](evals/rag/generation.py) (`run_calibration`) reports:

- **judge_human_agreement** — fraction of the 15 axis/item pairs where the
  judge lands within **one 0.25 bucket** of the human label.
- **judge_human_mae** — mean absolute error over those 15 pairs.

The per-item human-vs-judge breakdown is kept under `calibration.items` in
`eval_report.json`. `judge_human_agreement` is **gated** (floor 0.60): if it
drops, the judge has drifted — its scores are no longer trustworthy and the
build fails, because stale generation scores are worse than none.

> Calibration is corpus-independent (the contexts are embedded in the labels
> file), so it runs anywhere the judge runs — CI included.

### When the judge is unavailable

The generation half needs a Groq key. Without `GROQ_API_KEY`, the suite still
reports retrieval metrics and records `generation: skipped` — it **never** fails
a build just because the key is absent. CI turns generation scoring on when the
optional `GROQ_API_KEY` repo secret is set (see the workflow `env:` block).

---

## Classification eval

[evals/classification/run.py](evals/classification/run.py) — accuracy and
macro-F1 over the four issue classes (bug / feature / docs / question).

- **In CI** the 256 MB DistilBERT checkpoint isn't shipped, so the suite falls
  back to a TF-IDF + LogisticRegression baseline trained on the fixture — a
  reproducible signal whose floor catches a total breakdown (single-class
  prediction → macro_f1 = 0).
- **Locally**, with `models/classifier/v1/` present, it runs the real
  fine-tuned model (`mode: deberta`).
- A separate 25-issue hand-curated comparison
  ([evals/classification/three_way_run.py](evals/classification/three_way_run.py))
  scores all three approaches (DL / ML / LLM) — see
  [DECISIONS.md](DECISIONS.md) ADR-001.

---

## Regression gating

[evals/diff.py](evals/diff.py) enforces two gates per metric from
[eval_thresholds.yaml](eval_thresholds.yaml):

- **min_absolute** — hard floor, regardless of history.
- **max_relative_drop** — fail if the metric fell more than X% below the last
  green `main` run.

A metric absent from the report (e.g. the `gen_*` keys when the judge is
skipped) is silently ignored — no false regression. A suite with
`status: error` fails the build immediately.

## Running locally

```bash
# Full RAG suite (retrieval + generation + calibration) against the prod corpus
docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py

# Both suites, combined report (what CI runs)
python evals/run_all.py --output eval_report.json
```
