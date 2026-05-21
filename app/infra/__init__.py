# app/infra/__init__.py
"""Infrastructure adapters — every external system the app talks to has
exactly one module here. Service code imports from these adapters; nothing
under app/services/ or app/api/ should instantiate a third-party client
directly.

Adapters:
  - vault.py        : HashiCorp Vault — secret loading at boot
  - blob.py         : MinIO  — eval reports, model manifests, training plots,
                              per-conversation chunk snapshots
  - redis.py        : Redis  — short-term conversation history backing store
  - llm.py          : LLM providers (Groq today) — chat-completions client
  - model_server.py : the in-cluster model-server FastAPI service —
                              classify / extract / summarize / embed / rag_search
  - tracing.py      : Langfuse v4 — observability client
  - redaction.py    : Secret-scrub layer (regex over chat input / logs)

Boundary modules also living here:
  - db.py            : back-compat shim re-exporting from app/db/session.py
  - middleware.py    : Starlette HTTP middleware
  - error_handlers.py: FastAPI exception handlers
"""
