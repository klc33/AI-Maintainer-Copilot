# Architecture

A deeper write-up of how the Maintainer's Copilot fits together. For the
project overview see [README.md](README.md); for day-2 operations see
[RUNBOOK.md](RUNBOOK.md); for the RAG pipeline specifically see
[rag-works.md](rag-works.md).

---

## 1. System context

The Copilot is a self-hosted chatbot for Terraform-project maintainers. It
triages GitHub issues with a fine-tuned classifier, retrieves docs and prior
issues with hybrid RAG, summarizes long threads, and remembers facts per user.
End users reach it through an embeddable widget; admins manage widget configs
through a Streamlit panel.

It runs as **11 Docker Compose services** split into three planes:

```
                    ┌─────────────────── frontends ──────────────────┐
                    │  streamlit (8501)   widget bundle   host (9090) │
                    └───────────────────────┬────────────────────────┘
                                             │ HTTP
                    ┌──────────────────── app plane ─────────────────┐
                    │  api (8000, FastAPI)      model-server (8001)   │
                    └───────────────────────┬────────────────────────┘
                                             │
   ┌────────────────────── infra plane ──────┴──────────────────────────┐
   │  db (Postgres+pgvector)  redis  minio  vault  langfuse              │
   └─────────────────────────────────────────────────────────────────────┘
```

`migrate` is a one-shot job (alembic upgrade head), not a long-running service.

---

## 2. Service topology & boot order

Compose declares dependencies with `depends_on: condition: service_healthy`,
so the stack self-orders:

```
db  redis  minio  vault          infra — all must be healthy first
        ↓
       langfuse                  observability backend
        ↓
       migrate                   one-shot: alembic upgrade head, then exits
        ↓
       api          model-server app plane
        ↓
       streamlit    widget       admin UI + widget bundle build
        ↓
       host                      demo page that proxies /api → api
```

| Service | Port | Role |
|---|---|---|
| `db` | 5432 | Postgres 16 + pgvector — relational data + 768-dim vectors |
| `redis` | 6379 | short-term chat history (24 h TTL) |
| `minio` | 9000/9001 | object storage — eval reports, model manifests, training plots, conversation snapshots |
| `vault` | 8200 | secrets (dev mode — see [RUNBOOK.md](RUNBOOK.md)) |
| `langfuse` | 3000 | LLM tracing/observability (v4, self-hosted) |
| `migrate` | — | runs Alembic migrations once and exits |
| `api` | 8000 | FastAPI app — the system's front door |
| `model-server` | 8001 | classifier, NER, summarizer, embedder, RAG retrieval |
| `streamlit` | 8501 | admin UI |
| `widget` | 8080 | nginx serving the single-file Preact bundle |
| `host` | 9090 | demo host page that embeds the widget |

See [RUNBOOK.md](RUNBOOK.md) "Boot order" for timing and first-boot model
downloads.

---

## 3. The API — layered architecture

The `api` container is the heart of the system, and the one rule that keeps it
maintainable is a **strict layering**:

```
   HTTP request
        │
        ▼
   app/api/          ROUTERS — HTTP shape only. Parse/validate request,
        │            call one service, shape the response. No SQLAlchemy,
        │            no Redis, no external SDKs.
        ▼
   app/services/     SERVICES — business logic. Orchestrate repositories
        │            and infra adapters. The only layer allowed to "decide".
        ▼
   app/repositories/ REPOSITORIES — persistence only. asyncpg SQL and
        │            Redis commands. No business rules.
        ▼
   app/db/  +  Postgres / Redis / MinIO / Vault
```

The dependency arrow points **one way only**: `api → services → repositories
→ db`. A router never imports a repository; a repository never imports a
service. This is what makes a change local — swapping a query touches one
repository file, swapping an LLM provider touches one infra adapter.

### `app/api/` — routers (HTTP only)

| File | Endpoints |
|---|---|
| `auth.py` | register / login / logout (fastapi-users, JWT) |
| `chat.py` | the chatbot streaming endpoint |
| `memory.py` | per-user memory read |
| `widget.py` | widget embed page + widget chat (session-JWT auth) |
| `widget_admin.py` | admin CRUD for widget configs |

Routers get their cross-cutting dependencies (auth gate, DB session) from
`app/depends.py` via FastAPI's `Depends()` — see §5.

### `app/services/` — business logic

| File | Responsibility |
|---|---|
| `auth.py` | fastapi-users wiring; holds the Vault-loaded `JWT_SECRET` |
| `chatbot.py` | the agentic loop — Groq function-calling, tool dispatch (`execute_tool`), Langfuse span, conversation snapshots |
| `memory.py` | write/recall per-user memories (embeds, redacts, dedups) |
| `widget.py` | widget config resolution, CSP origin handling |

### `app/repositories/` — persistence

| File | Backing store |
|---|---|
| `memories.py` | `memories` table (pgvector) |
| `audit_log.py` | `audit_log` table |
| `conversation_history.py` | Redis (24 h TTL) |
| `widget_configs.py` | `widget_configs` table |

### `app/db/` — SQLAlchemy schema

`app/db/session.py` is the **canonical** home for the async engine
(`create_async_engine`, `pool_pre_ping=True`), the `async_session_factory`,
the declarative `Base`, and the `get_session` FastAPI dependency.
`app/db/models.py` defines one ORM model per table: `User`, `AuditLog`,
`Chunk`, `Memory`, `WidgetConfig`. `app/infra/db.py` and `app/domain/models.py`
are thin **back-compat shims** that re-export from `app.db`.

### `app/domain/` — contracts

Pydantic request/response `schemas.py`, `exceptions.py` (domain errors mapped
to HTTP by `app/infra/error_handlers.py`), and ORM re-exports.

---

## 4. `app/infra/` — adapters for external systems

Every external system is reached through exactly one adapter module. Service
code imports the adapter, never the SDK — so the blast radius of "switch
vendors" is one file.

| Adapter | Wraps |
|---|---|
| `vault.py` | HashiCorp Vault — secret load at boot |
| `blob.py` | MinIO — eval reports, model manifests, snapshots |
| `redis.py` | Redis connection |
| `llm.py` | Groq chat-completions client (`get_chat_client()`) |
| `model_server.py` | HTTP client to the model-server (`classify`, `extract_entities`, `summarize`, `embed`, `rag_search`) |
| `tracing.py` | Langfuse — wrapped by `_RedactingLangfuseClient` so spans are scrubbed before they ship |
| `redaction.py` | regex secret-scrub layer — see [SECURITY.md](SECURITY.md) |
| `db.py` | back-compat shim → `app.db.session` |
| `middleware.py`, `error_handlers.py` | exception logging + domain-error → HTTP mapping |

The **redaction layer is special**: it runs *before* anything leaves the
service boundary — every log line (`structlog_redactor` processor),
every Langfuse span (`_RedactingLangfuseClient`), and every memory write.

---

## 5. Dependency injection

`app/depends.py` is the single hub for FastAPI dependencies. Routers do:

```python
from app.depends import current_active_user, require_admin

@router.get("/admin/widgets")
async def list_widgets(user: User = Depends(require_admin)):
    ...
```

rather than constructing `Depends` factories inline. It exposes:

- `current_active_user` — the "I'm logged in" gate (fastapi-users).
- `require_admin` — built on top; 403s anyone who isn't a superuser or
  `role='admin'`.
- `get_db_session` — re-exported from `app.db.session` so all DI lives in
  one place.

New cross-cutting dependencies (rate limits, idempotency keys, tenant scoping)
get added here, not scattered across routers.

---

## 6. The model-server

A **separate FastAPI container** (`model_server/`) so the heavy ML
dependencies (torch, transformers, sentence-transformers, spaCy) and the
720 MB of model weights never bloat the api image, and so it can scale or be
GPU-scheduled independently.

| Endpoint | Backed by |
|---|---|
| `POST /classify` | fine-tuned DistilBERT (`models/classifier/v1/`) |
| `POST /extract` | spaCy NER (`ner.py`) |
| `POST /summarize` | Groq `llama-3.1-8b-instant` (`summarizer.py`) |
| `POST /embed` | `bge-base-en-v1.5` — 768-dim vectors for memory + RAG |
| `GET  /rag/search` | the hybrid RAG pipeline (`rag_retrieval.py`) |
| `GET  /health` | reports `classifier_loaded` / `groq_loaded` |

It loads the classifier and the Groq key (from Vault) on startup; a Vault
miss soft-fails so classifier/NER/RAG still serve while `/summarize` errors at
call time. The RAG pipeline is documented separately in
[rag-works.md](rag-works.md).

---

## 7. Request flow — one chat turn

```
widget / streamlit
   │  POST /chat  (JWT)
   ▼
app/api/chat.py            validate, resolve user, stream
   │
   ▼
app/services/chatbot.py    open Langfuse span; load history (Redis);
   │                       call Groq with the tool registry
   │
   ├── LLM asks for a tool ─► execute_tool():
   │        classify_issue ───► model_server.classify
   │        extract_entities ─► model_server.extract_entities
   │        summarize_thread ─► model_server.summarize
   │        search_knowledge ─► model_server.rag_search ──► /rag/search
   │        write_memory ─────► services.memory.write_memory
   │
   ▼
final answer streamed back; turn appended to Redis history;
search_knowledge results snapshotted to MinIO; span closed (redacted)
```

The tool **schemas** live in `tools/registry.py`; the tool **dispatch /
side-effects** live in `chatbot.py::execute_tool`. Widget configs carry an
`enabled_tools` allowlist that filters the schema list per session.

---

## 8. Data stores

| Store | Holds |
|---|---|
| **Postgres** | `users`, `audit_log`, `chunks` (RAG corpus + pgvector embeddings), `memories` (pgvector), `widget_configs`. Schema is owned by Alembic — `migrations/versions/001`…`006`. |
| **Redis** | short-term conversation history, 24 h TTL. Loss is non-fatal — the next turn starts fresh. |
| **MinIO** | eval reports (`eval-reports` bucket), model manifests, training plots, conversation snapshots. |
| **Vault** | `secret/shared/{jwt,groq,langfuse}` — loaded into the process at boot. Dev mode → ephemeral; see [RUNBOOK.md](RUNBOOK.md). |

---

## 9. Boot checks

`app/main.py` runs an ordered set of checks in the FastAPI lifespan; any
failure is `sys.exit(1)` so the container restarts loudly rather than serving
half-configured:

1. **Vault healthy** — reachable and unsealed.
2. **DB at head** — `alembic_version` matches a current migration head
   (read from the script directory, not a hardcoded string).
3. **JWT secret** — loaded from Vault into the auth module.
4. **Groq key** — loaded from Vault into `os.environ`.
5. **Langfuse keys** — loaded from Vault into `os.environ`.

A failed check names itself in the log (`Boot check failed error=…`) — the
[RUNBOOK.md](RUNBOOK.md) cheatsheet maps each one to a fix.

---

## 10. Frontends

- **widget/** — Preact + vanilla CSS, built by Vite into a single IIFE
  bundle, served by nginx. Embeds via `<script data-widget-id="…">`; an
  iframe with `postMessage` resizing. The api serves `/widget/{id}/embed` with
  a `Content-Security-Policy: frame-ancestors` derived from the config's
  `allowed_origins`.
- **streamlit_app/** — admin UI (admin-only; widget-config CRUD, eval views).
- **demo/host/** — a static login/register/widget-chooser SPA behind an nginx
  proxy that forwards `/api` to the api service.

---

## 11. Observability & security

- **Tracing** — every chat turn is a Langfuse span. The Langfuse client is
  wrapped by `_RedactingLangfuseClient` so span inputs/outputs are scrubbed.
- **Logging** — structlog, with `structlog_redactor` as the *first* processor.
- **Redaction** — a regex catalog ([app/infra/redaction.py](app/infra/redaction.py))
  runs before any log line, trace span, or memory write leaves the boundary.
  Threat model + per-pattern justification in [SECURITY.md](SECURITY.md).
- **Auth** — fastapi-users with JWT; the signing key comes from Vault.
  Rotating it logs everyone out; widget iframes re-mint sessions automatically.

---

## 12. CI

GitHub Actions (`.github/workflows/eval.yml`) runs both eval suites — RAG and
classification — on **every push and PR**, writes `eval_report.json`, and
diffs it against the last green `main` build. A regression past the floors in
`eval_thresholds.yaml` blocks the merge. Methodology: [EVALS.md](EVALS.md).

---

## Running the tests

Two kinds of automated checks: **pytest unit tests** and the **eval suites**.

### Unit tests (pytest)

| Path | Covers |
|---|---|
| `app/infra/tests/test_redaction.py` | redaction layer — 22 tests; no fake secret escapes via logs, traces, or memory |
| `tests/unit/`, `tests/integration/`, `tests/smoke/` | empty scaffold; pytest auto-discovers any `test_*.py` added |

```bash
docker compose exec api /app/.venv/bin/python -m pytest      # in-container (all deps present)
uv run --group dev --group api pytest                        # on the host
```

### Eval suites (RAG + classification)

```bash
docker compose exec model-server /app/.venv/bin/python /app/evals/run_all.py --output eval_report.json
```

Full detail — commands, filters, and the CI gating — is in
[RUNBOOK.md](RUNBOOK.md) "Running the tests" and [EVALS.md](EVALS.md).
