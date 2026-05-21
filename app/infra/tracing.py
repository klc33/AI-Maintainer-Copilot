# app/infra/tracing.py
"""Langfuse adapter — wraps the SDK so every payload that crosses the
network is redacted first.

The wrapper is transparent for callers: existing code that does
`lf_client.start_as_current_observation(input=...)` keeps working as-is.
The wrapper recursively redacts the `input`, `output`, and `metadata`
kwargs before delegating, and also redacts arguments to `.update()` on
the returned span object.
"""
from __future__ import annotations

import os

import langfuse

from app.infra.redaction import redact_deep


# ── Internal proxies ───────────────────────────────────
class _RedactingSpan:
    """Forwards every attribute access to the real Langfuse span, but
    intercepts `.update(...)` to redact string args before they hit the
    SDK (and thus before they're shipped to the Langfuse server)."""
    __slots__ = ("_span",)

    def __init__(self, real_span):
        self._span = real_span

    def update(self, **kwargs):
        return self._span.update(**{k: redact_deep(v) for k, v in kwargs.items()})

    def __getattr__(self, name):
        return getattr(self._span, name)


class _RedactingObservationCtx:
    """Context manager that wraps Langfuse's observation CM and yields a
    `_RedactingSpan` instead of the raw span."""
    __slots__ = ("_cm", "_span")

    def __init__(self, real_cm):
        self._cm = real_cm
        self._span = None

    def __enter__(self):
        self._span = self._cm.__enter__()
        return _RedactingSpan(self._span)

    def __exit__(self, exc_type, exc, tb):
        return self._cm.__exit__(exc_type, exc, tb)


class _RedactingLangfuseClient:
    """Thin proxy around langfuse.Langfuse. Only intercepts
    `start_as_current_observation` (since that's the entry point we use);
    every other attribute is forwarded to the underlying client."""
    __slots__ = ("_client",)

    def __init__(self, real_client):
        self._client = real_client

    def start_as_current_observation(self, **kwargs):
        redacted = {k: redact_deep(v) for k, v in kwargs.items()}
        return _RedactingObservationCtx(self._client.start_as_current_observation(**redacted))

    def __getattr__(self, name):
        return getattr(self._client, name)


# ── Public API ─────────────────────────────────────────
def get_langfuse_client():
    """Return a redaction-wrapped Langfuse client configured from env vars
    that the boot check loaded from Vault."""
    real = langfuse.Langfuse(
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-dev"),
        secret_key=os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-dev"),
        host=os.environ.get("LANGFUSE_HOST", "http://langfuse:3000"),
    )
    return _RedactingLangfuseClient(real)
