# Deliverables

**Project 7 — Maintainer's Copilot**

- **Repo:** https://github.com/klc33/AI-Maintainer-Copilot
- **Tag:** `v0.1.0-week7`
- **Dataset:** hashicorp/terraform issues — 9,605 train / 1,373 val / 2,058 test
  (+ 687 held out for RAG)
- **Classification (macro-F1):** Classical = **0.80** | Fine-tuned = **0.96** | LLM = **0.71**
- **Deployment choice:** Fine-tuned DistilBERT — best macro-F1 with zero
  per-call API cost and ~140 ms CPU latency (see [DECISIONS.md](DECISIONS.md) ADR-001).
- **Embedding model:** `BAAI/bge-base-en-v1.5` (768-dim) — strong retrieval
  quality at a modest size, open-source and CPU-friendly, no embedding-API dependency.
- **RAG:** hit@5 = **0.32** | MRR@10 = **0.25** | Faithfulness = *pending run* | Answer relevancy = *pending run*
- **Long-term memory type:** semantic — per-user facts (`summary` + `entities`),
  embedded and recalled by vector similarity (the `memories` table).
- **Tracing backend:** Langfuse — open-source and self-hostable (traces never
  leave the stack), purpose-built for nested LLM spans + cost/latency tracking.
- **Widget bundle size:** ~6.5 KB gzipped (`widget.js`; 15.5 KB raw). Loader: 0.5 KB gzipped.
- **LLM:** Groq — `llama-3.1-8b-instant` (chatbot + summarizer).

---

## Notes & provenance

- **Classification F1** — the three values are macro-F1 on the **25-issue
  hand-curated comparison set**, the only apples-to-apples run across all three
  approaches ([evals/classification/three_way_results.json](evals/classification/three_way_results.json),
  written by `three_way_run.py`). On the larger held-out **val** set the
  deployed fine-tuned model scores macro-F1 **0.66** / accuracy **0.80**
  (`models/classifier/v1/model_card.json`).
- **RAG metrics** — hit@5 / MRR@10 are from the latest recorded retrieval run
  over the 25-question golden set (`datasets/rag_eval_report.json`).
  **Faithfulness** and **Answer relevancy** are generation-side metrics scored
  by the frozen LLM judge added this week; they have not been run yet (the
  judge needs a Groq key). Produce them with:
  ```bash
  docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py
  ```
  See [EVALS.md](EVALS.md) and [rag-works.md](rag-works.md) for methodology.
- **LLM** — `llama-3.1-8b-instant` is the product LLM. The eval judge uses a
  separate, pinned `llama-3.3-70b-versatile` (eval infrastructure only).
- **Tag** — `v0.1.0-week7` is the intended release tag; it is not yet created
  in git (`git tag` is currently empty).
