# Maintainer's Copilot

A self-hosted chatbot for Terraform-project maintainers. It triages GitHub issues with a fine-tuned classifier, retrieves relevant docs and prior issues with hybrid RAG, summarizes long threads, and remembers facts per user. End users hit it via an embeddable widget; admins manage widget configurations through a Streamlit panel.

## Production model

The `/classify` endpoint is served by a fine-tuned **DistilBERT** (`distilbert-base-uncased`, 67 M params). On the held-out validation set it lands at **accuracy 0.80 / macro-F1 0.66**, with all four classes (bug / feature / docs / question) above F1=0.37. On a 25-issue hand-curated set it hits **0.96 / 0.96**.

The TF-IDF + Logistic Regression baseline is kept around for CI smoke tests (it doesn't need the 256 MB model checkpoint to be available) and as a safety floor. The Groq LLM is **not** used for classification — see [DECISIONS.md](DECISIONS.md) ADR-001 for the full three-way comparison and the deployment-choice defense.

The summarization tool *does* use Groq's `llama-3.1-8b-instant`, because open-ended generation isn't a labeled classification problem.

## Stack

| Layer | Tech |
|---|---|
| API | FastAPI + asyncpg + fastapi-users (JWT auth) |
| Database | Postgres 16 + pgvector |
| Cache | Redis 7 (short-term chat history) |
| Object storage | MinIO (eval reports, model manifests, training plots, conversation snapshots) |
| Secrets | HashiCorp Vault (dev mode) |
| Observability | Langfuse v4 (self-hosted) |
| LLM provider | Groq |
| Fine-tuned classifier | DistilBERT served by an in-cluster FastAPI model-server |
| RAG | bge-base-en-v1.5 (embedder) + Postgres FTS + RRF + bge-reranker-base |
| Admin UI | Streamlit |
| Embed widget | Preact + vanilla CSS, single-file bundle served by nginx |
| Demo host | Static page + nginx proxy |
| CI | GitHub Actions — eval suites with regression gating |

## Quick start

1. **Bring up the stack:**

   ```bash
   cp .env.example .env       # then edit VAULT_TOKEN
   docker compose up -d
   ```

2. **Seed Vault secrets** (one-time — see [DECISIONS.md](DECISIONS.md) for the commands). At minimum you need a JWT secret, a Groq API key, and Langfuse keys.

3. **Create an admin user:**

   ```bash
   docker compose exec api /app/.venv/bin/python /app/scripts/create_admin_user.py
   # default: admin@example.com / admin123 — change via ADMIN_EMAIL/ADMIN_PASSWORD env
   ```

4. **Try it:**

   | URL | What you get |
   |---|---|
   | <http://localhost:9090> | Demo host page — login or register, pick a widget, chat |
   | <http://localhost:8501> | Streamlit admin UI (admin-only — sign in with the admin you just made) |
   | <http://localhost:8000/docs> | FastAPI's OpenAPI playground |
   | <http://localhost:3000> | Langfuse (admin@example.com / admin123 by default) |
   | <http://localhost:9001> | MinIO console (minioadmin / minioadmin) |

## Tests

```bash
docker compose exec api /app/.venv/bin/python -m pytest                        # unit tests (pytest)
docker compose exec model-server /app/.venv/bin/python /app/evals/run_all.py   # RAG + classification evals
```

The repo has pytest unit tests and the RAG + classification eval suites; the
eval suites also run in CI on every push and PR. Full commands, the test
layout, and CI gating are in [RUNBOOK.md](RUNBOOK.md) "Running the tests";
eval methodology is in [EVALS.md](EVALS.md).

## Repo layout

```
app/
├── api/             ← FastAPI routers. HTTP shape only — no DB/Redis/external imports.
├── services/        ← Business logic. Orchestrates repos + infra adapters.
├── repositories/    ← Persistence only. asyncpg SQL + Redis commands.
├── infra/           ← Adapters for external systems:
│                       vault, blob (MinIO), redis, llm (Groq), model_server,
│                       tracing (Langfuse), redaction.
├── db/              ← SQLAlchemy schema + async engine + session factory.
├── domain/          ← Pydantic schemas, exceptions, ORM model re-exports.
├── depends.py       ← FastAPI Depends() hub (auth gates, future rate limits).
└── main.py          ← Lifespan + boot checks + router wiring + structlog config.

prompts/             ← Chat / tool prompts (.md, loaded by prompts.get_prompt).
tools/               ← LLM tool schemas (the chatbot's function-calling registry).
scripts/             ← One-shot scripts (admin creation, model-artifact upload).

model_server/        ← Separate container: classifier, NER, summarizer, embedder, reranker.
widget/              ← Preact widget source + Vite build.
streamlit_app/       ← Admin UI.
demo/host/           ← Demo host page that embeds the widget.

migrations/          ← Alembic migrations (Postgres schema).
evals/               ← Eval harness (RAG + classification, three-way comparison).
notebooks/           ← Colab notebooks (currently: classifier retraining).

models/              ← Trained model checkpoints (gitignored; 256 MB+).
datasets/            ← Training/eval CSVs (gitignored; large).
docker/              ← Per-service Dockerfiles + nginx configs.
docs/                ← Markdown docs (RAG corpus + model card).
```

## What's where in this README family

- **README.md** (this file) — overview, quick start, layout.
- **[DECISIONS.md](DECISIONS.md)** — architectural decision records. The classifier-choice ADR-001 lives here. Also: Vault setup commands.
- **[SECURITY.md](SECURITY.md)** — redaction layer threat model + pattern justification.
- **[RUNBOOK.md](RUNBOOK.md)** — day-2 operations: how to restart things, recover from common failures, retrain the model, manage widgets, rotate secrets.
- **[ARCH.md](ARCH.md)** — deeper architecture write-up: service topology, the layered api (api → services → repositories → db), infra adapters, request flow, boot checks.
- **[rag-works.md](rag-works.md)** — how RAG works here: the retrieval techniques used, which files hold the pipeline, and where it's evaluated.
- **[EVALS.md](EVALS.md)** — eval methodology: RAG retrieval + generation metrics, the frozen judge and its hand-label calibration, classification, and CI regression gating.
- **[notebooks/README.md](notebooks/README.md)** — how to retrain the classifier on Colab.

## License

TBD.
