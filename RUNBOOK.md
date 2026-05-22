# Runbook

Day-2 operations for the Maintainer's Copilot stack. Aimed at "I need to do X right now" — see [README.md](README.md) for the project overview and [SECURITY.md](SECURITY.md) for the redaction-layer threat model.

---

## Boot order (`docker compose up -d`)

The compose file declares this order via `depends_on: condition: service_healthy`:

```
db  redis  minio  vault          ← infra, all must be healthy first
        ↓
       langfuse                  ← starts after db (uses its own DB on the same instance)
        ↓
       migrate                   ← one-shot: alembic upgrade head
        ↓
       api                       ← needs vault (loads secrets) + db (boot check verifies migration head)
       model-server              ← needs vault (GROQ_API_KEY) + db (rag chunks) + minio (datasets) + HF cache volume
        ↓
       streamlit  widget         ← depend on api being up
        ↓
       host                      ← demo host that proxies /api to the api service
```

A clean `docker compose up -d` takes ~30 s on a warm machine (HF model cache populated, BuildKit cache populated). First-ever boot pulls 720 MB of bge models → ~3–5 min.

---

## "X isn't working" cheatsheet

| Symptom | Most likely cause | Fix |
|---|---|---|
| api container is `Restarting` / `Exited (3)` | One of the boot checks failed. | `docker compose logs api | grep "Boot check failed"` — the `error=` field names the failed check. |
| api boot fails: `Failed to read secret at secret/shared/jwt … InvalidPath` | **Vault lost its secrets.** Vault runs in dev mode — secrets are in-memory and vanish on every Vault container restart. | `bash scripts/seed_vault.sh` then `docker compose up -d api model-server`. The script is idempotent. |
| `Database migration not at head. Current: X, expected one of: [Y]` on boot | A migration exists in the repo that hasn't been applied. | `docker compose run --rm migrate` — runs `alembic upgrade head` against the DB and exits. |
| model-server is `unhealthy` and `/health` from the api times out | bge models still downloading on first boot, OR the HF cache volume got wiped. | `docker compose logs model-server | grep "Loading weights"` — you should see two model loads (199 weights + 201 weights) then "Application startup complete". If only one is loading, give it 2 more minutes (the rate-limit-throttled cold download). |
| Chat tool returns `"classifier unavailable"` / `"RAG search unavailable"` | model-server down or unreachable. | `docker compose ps model-server` — restart it with `docker compose restart model-server`. |
| Widget shows "Widget unavailable — No active widget with id X" | The widget_id in the embed script doesn't exist or is `is_active=false`. | Streamlit admin → Widget Configs → toggle Active, OR change the `data-widget-id` on the host page. |
| Widget JWT 401 in widget chat | JWT secret was rotated but old session tokens are cached in the browser. | Reload the host page — the embed bootstrap re-mints a session JWT. |
| Conversation history isn't carrying over | Redis got restarted. | History is TTL'd at 24 h; nothing to recover, the next turn starts fresh. |
| Streamlit shows "Streamlit access is restricted to admin users" | User isn't admin. | Create an admin via `scripts/create_admin_user.py` (see below) or set `role='admin'` on the user row in Postgres. |
| Eval CI job fails with regression | Either a real regression, or thresholds tightened past current performance. | See "CI eval regression" below. |

---

## Common one-liners

### Restart a single service

```bash
docker compose restart api          # graceful — does NOT pick up code changes baked into the image
docker compose up -d --build api    # rebuild + recreate — needed after edits to app/ or pyproject
docker compose up -d --force-recreate api   # recreate without rebuild (use after env / compose changes)
```

### View live logs

```bash
docker compose logs -f api          # tail -f equivalent
docker compose logs --tail=80 model-server | grep -v "GET /health"
```

### Run a query against Postgres

```bash
docker compose exec db psql -U copilot -d copilot
# inside psql:
#   \dt              list tables
#   SELECT count(*) FROM memories;
#   \q               quit
```

### Inspect MinIO buckets

```bash
docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker compose exec minio mc ls --recursive local/
```

---

## Running the tests

Two kinds of automated checks live in this repo: **pytest unit tests** and the
**eval suites**. CI ([.github/workflows/eval.yml](.github/workflows/eval.yml))
runs the eval suites on every push and PR; the commands below run everything
locally.

### Unit tests (pytest)

The pytest tree — three tiers under `tests/`, plus a test co-located with the
code it covers:

| Path | What it covers |
|---|---|
| `tests/unit/` | Pure-logic tests — the tool registry, the RAG judge's score parser, the eval-diff helpers, and the eval data-file schemas (golden set, human labels, thresholds). |
| `tests/smoke/` | Wiring checks — the docs are present, `docker-compose.yml` parses, prompts load, and the FastAPI `api` app constructs with its routers. |
| `tests/integration/` | Live-stack checks — Postgres is migrated, the model-server and api answer `/health`. Each **skips cleanly** when its service isn't reachable. |
| `app/infra/tests/test_redaction.py` | The redaction layer — 22 tests asserting no fake secret escapes via logs, Langfuse spans, or memory writes. |

Run them inside the `api` container — its venv already has every dependency:

```bash
docker compose exec api /app/.venv/bin/python -m pytest
```

Or on the host with uv (pull the test + app + evals dependency groups):

```bash
uv run --group dev --group api --group evals python -m pytest                 # everything
uv run --group dev --group api --group evals python -m pytest tests/unit -v   # one tier, verbose
uv run --group dev --group api --group evals python -m pytest -k redact        # filter by name
```

The integration tier needs a running stack; without one those tests skip and
the rest still pass. `conftest.py` points `DATABASE_URL` / `MODEL_SERVER_URL`
at the docker-compose host-mapped ports, so `docker compose up -d` is all it
takes for the integration tier to run for real.

There is no `[tool.pytest.ini_options]` block — discovery is the default
(`test_*.py`, skipping `.venv/` and `node_modules/`). The root `conftest.py`
puts the project root on `sys.path` and sets local-dev env defaults so
unit/smoke tests need no stack.

### Eval suites (RAG + classification)

These are behavioral tests of the ML/RAG system, not pytest. They need a
running Postgres with an ingested corpus. See [EVALS.md](EVALS.md) for the
methodology; the commands:

```bash
# Both suites → combined eval_report.json (exactly what CI runs)
docker compose exec model-server /app/.venv/bin/python /app/evals/run_all.py --output eval_report.json

# RAG only — retrieval + generation + judge calibration
docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py

# Classification only
docker compose exec model-server /app/.venv/bin/python /app/evals/classification/run.py
```

A regression against the previous green build is what fails CI — see
[CI eval regression](#ci-eval-regression) below.

---

## Creating / resetting the admin user

```bash
docker compose exec api /app/.venv/bin/python /app/scripts/create_admin_user.py
# → "Admin user 'admin@example.com' created in copilot DB."
```

Pass `ADMIN_EMAIL` / `ADMIN_PASSWORD` env vars to use different credentials. The script is idempotent — it deletes any existing row with the same email before inserting.

---

## Vault secrets — seeding and rotating

### Vault is in dev mode (secrets are ephemeral)

`docker-compose.yml` runs Vault with `VAULT_DEV_ROOT_TOKEN_ID`. Dev mode keeps
everything **in memory** — every Vault container restart wipes all secrets,
and the api/model-server then fail their boot checks. This is the single most
common "it worked yesterday" failure.

**Recovery (after any Vault restart):**

```bash
bash scripts/seed_vault.sh
docker compose up -d api model-server
```

`scripts/seed_vault.sh` re-seeds the three required secrets:

| Secret | Source | Notes |
|---|---|---|
| `secret/shared/jwt` | freshly generated (openssl/python) | Only generated if missing — re-running the script won't rotate it. |
| `secret/shared/groq` | `GROQ_API_KEY` in `.env` | |
| `secret/shared/langfuse` | `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` in `.env` | |

The script is idempotent — safe to run any time. If a key is missing from
`.env` it errors out clearly rather than seeding a half-broken Vault.

### Rotating a secret on purpose (leak / schedule)

1. Update the value in `.env` (for Groq/Langfuse) or let the script regenerate (for JWT — delete `secret/shared/jwt` first so the "only if missing" check regenerates it).
2. `bash scripts/seed_vault.sh`
3. `docker compose up -d api model-server` — both reload secrets from Vault at boot.
4. *(Optional)* Confirm: `docker compose logs api | grep "loaded from Vault"`.

Rotating the **JWT secret** invalidates every existing user session AND every widget session JWT. Users get logged out on next request; widget iframes re-mint sessions automatically.

---

## Retraining the classifier

The fine-tuned DistilBERT model lives at `models/classifier/v1/`. To retrain (you'll need a GPU — Colab free tier is fine):

1. Open `notebooks/train_classifier.ipynb` in Google Colab.
2. Follow `notebooks/README.md` — upload `datasets/train.csv` and `datasets/val.csv`, run all cells.
3. Download the resulting `classifier_v2.zip` (~870 MB).
4. Deploy:

   ```bash
   docker compose stop model-server
   cd models/classifier/v1 && rm -rf ./* && unzip /path/to/classifier_v2.zip
   docker compose start model-server
   # wait ~30 s for the model-server health probe to flip green
   docker compose exec model-server cat /app/models/classifier/v1/model_card.json
   ```

5. Upload the new manifest to MinIO:

   ```bash
   docker compose exec -w /app model-server /app/.venv/bin/python /app/scripts/upload_model_artifacts.py
   ```

If the new model is meaningfully better/worse, update [DECISIONS.md](DECISIONS.md) ADR-001 with the new numbers and (if appropriate) tighten the thresholds in `eval_thresholds.yaml`.

---

## Managing widgets

The admin Streamlit page at <http://localhost:8501> → "Admin: Widget Configs" is the canonical interface. Each widget has:

| Field | What it does |
|---|---|
| `widget_id` | Slug used in `<script data-widget-id="…">`. **Primary key — can't be changed** without breaking every existing embed. |
| `name` | Display label. Cosmetic. |
| `description` | Shown to end users on the demo host's chooser. |
| `allowed_origins` | CSV. Becomes the `frame-ancestors` CSP directive on `/widget/{id}/embed`. `*` allows any framer; empty list maps to `'none'`. |
| `theme.color` | CSS accent. |
| `theme.position` | `bottom-right` or `bottom-left`. |
| `theme.greeting` | First assistant message shown when the widget opens. |
| `enabled_tools` | Subset of [tools/registry.py](tools/registry.py)'s tool names. Filters what the LLM can call for this widget's sessions. |
| `is_active` | Soft delete. Inactive widgets return the "Widget unavailable" card. |

For programmatic management see the `/admin/widgets` endpoints in the OpenAPI playground at <http://localhost:8000/docs>.

---

## CI eval regression

The `Evals` workflow runs both eval suites on every push and PR. Failure modes:

| Failure | Diagnosis | Fix |
|---|---|---|
| RAG hit_at_5 dropped below floor | Probably a regression in the retrieval pipeline (`rag_retrieval.py` change), or fixture chunks got broken. | Run `docker compose exec model-server /app/.venv/bin/python /app/evals/rag/run.py` locally to see real numbers. If the change is intentional and the new numbers are still acceptable, lower the floor in `eval_thresholds.yaml`. |
| Classification macro_f1 dropped below floor | Almost always a problem with the eval fixture or the classifier. In CI we use a TF-IDF baseline (no DL checkpoint in CI). | Run locally with the real model: `evals/classification/run.py` reports `mode: deberta`. If even that is broken, retrain (see "Retraining the classifier" above). |
| First-ever push on a new branch fails the diff | There's no previous green run on `main` to compare against. | The diff falls back to absolute thresholds only — if those fail, the absolute floor is too high. Lower it in `eval_thresholds.yaml`. |
| "No files found with the provided path: eval_report.json" warning | The actual eval step crashed; no report was produced. | Click into the CI run, the failing step's logs are above the warning. |

---

## Wiping the DB / starting fresh

```bash
docker compose down -v          # ⚠️ deletes ALL volumes — postgres data, MinIO buckets, HF cache
docker compose up -d
docker compose run --rm migrate
docker compose exec api /app/.venv/bin/python /app/scripts/create_admin_user.py
# re-seed Vault (see DECISIONS.md)
```

This is a destructive operation. Don't run it on production.

---

## Debugging a leaked secret

If the redaction layer missed something:

1. Confirm the leak: grep the affected destination (logs / Langfuse spans / `memories` table) for the credential's distinctive prefix.
2. Add or tighten a pattern in `app/infra/redaction.py`. Add a fake-key entry to `FAKE` in `app/infra/tests/test_redaction.py` — the parametrized test catches it on the next CI run.
3. Document in [SECURITY.md](SECURITY.md).
4. **Rotate the leaked credential** regardless. The redaction fix only stops future leaks.

---

## Useful environment variables

| Var | Used by | Default |
|---|---|---|
| `DATABASE_URL` | api, model-server (RAG), migrate | `postgresql+asyncpg://copilot:changeme@db:5432/copilot` |
| `REDIS_URL` | api | `redis://redis:6379/0` |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | api (blob), model-server | `minio:9000` / `minioadmin` / `minioadmin` |
| `VAULT_ADDR` / `VAULT_TOKEN` | api, model-server | `http://vault:8200` / `root` |
| `MODEL_SERVER_URL` | api | `http://model-server:8001` |
| `MODEL_PATH` | model-server | `/app/models/classifier/v1` |
| `WIDGET_BUNDLE_URL` | api (embed page) | `http://localhost:8080/widget/widget.js` |
| `CONVERSATION_SNAPSHOT_KEEP_N` | api (chatbot) | `100` |
| `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | api, model-server | self-hosted Langfuse at `:3000` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `scripts/create_admin_user.py` | `admin@example.com` / `admin123` |
