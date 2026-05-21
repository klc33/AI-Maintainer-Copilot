# app/infra/tests/test_redaction.py
"""Exhaustive tests for the redaction layer.

Asserts that a message containing a fake API key never appears unredacted
in logs, traces, or memory — the three exits to the outside world.

These are unit tests with mocks for the IO side (no real Langfuse server,
no real Postgres). The flows being tested are:

  1. `redact()` strips every pattern in the catalog.
  2. `redact_deep()` walks nested dicts / lists.
  3. `structlog_redactor` removes secrets from log event dicts.
  4. The Langfuse client wrapper redacts `start_as_current_observation`
     kwargs and `.update()` kwargs before the SDK sees them.
  5. `services.memory.write_memory` redacts both `summary` and
     `entities` before they hit the repository (which would persist them).
"""
from __future__ import annotations

import asyncio
import pytest

import structlog

from app.infra.redaction import (
    PATTERNS,
    redact,
    redact_deep,
    structlog_redactor,
)


# A fake key per pattern. Each value is BUILT BY CONCATENATION on purpose:
# GitHub's push-protection secret scanner does static text matching, so a
# contiguous `sk_test_AAAA...` literal in source would (correctly, from its
# POV) trip the scanner and block the push. Splitting the prefix from the
# body means no complete secret-shaped literal exists in the file, while
# the assembled runtime value still matches our own PATTERNS regexes.
# These are NOT real credentials — the bodies are obviously synthetic.
_A = "A" * 36
FAKE = {
    "github_token":    "ghp" + "_" + _A,
    "github_fine_pat": "github" + "_pat_" + ("A" * 22) + "_" + ("B" * 59),
    "openai_key":      "sk" + "-" + _A,
    "groq_key":        "gsk" + "_" + ("A" * 40),
    "anthropic_key":   "sk" + "-ant-" + ("A" * 40),
    "stripe_live_key": "sk" + "_" + "live" + "_" + ("A" * 24),
    "stripe_test_key": "sk" + "_" + "test" + "_" + ("A" * 24),
    "aws_access_key_id": "AKIA" + ("A" * 16),
    "gcp_sa_email":    "tf-deploy@my-project" + ".iam.gserviceaccount.com",
    "gcp_api_key":     "AIza" + ("Sy" + "A" * 33),
    "slack_token":     "xox" + "b-" + ("1234567890-" + "A" * 24),
    "jwt_token":       "eyJ" + ("A" * 20) + "." + ("B" * 20) + "." + ("C" * 20),
    "url_basic_auth":  "https://admin:" + "hunter2" + "@internal.example.com/path",
    "auth_header":     "Authorization: " + "Bearer " + "eyJabc.def.ghijklmnop",
    "private_key_block":
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + ("A" * 40) + "\n"
        + ("B" * 40) + "\n"
        + "-----END RSA PRIVATE KEY-----",
}


# ── (1) Each pattern: a string containing the secret ends up redacted ──
@pytest.mark.parametrize("name,sample", list(FAKE.items()))
def test_every_pattern_is_redacted(name, sample):
    redacted = redact(f"context noise {sample} more noise")
    assert sample not in redacted, f"{name} leaked unredacted!"
    assert f"[REDACTED:{name}]" in redacted, (
        f"{name} matched but was tagged with a different pattern: {redacted!r}"
    )


# ── (2) redact_deep walks nested structures ──
def test_redact_deep_handles_nested_dicts_and_lists():
    fake = FAKE["github_token"]
    payload = {
        "outer": "fine",
        "secret_field": fake,
        "messages": [
            {"role": "user", "content": f"my token is {fake}"},
            {"role": "assistant", "content": "noted"},
        ],
        "meta": {"trace": {"x": f"and {fake} again"}},
    }
    out = redact_deep(payload)
    # The string anywhere in the structure should no longer appear.
    flat = repr(out)
    assert fake not in flat
    assert "[REDACTED:github_token]" in flat
    # Non-string values pass through untouched.
    assert out["outer"] == "fine"


def test_redact_deep_passes_through_non_strings():
    assert redact_deep(42) == 42
    assert redact_deep(None) is None
    assert redact_deep(3.14) == 3.14
    assert redact_deep(True) is True


def test_redact_on_non_string_returns_unchanged():
    """redact() is a no-op for non-strings so callers can apply it
    blindly via redact_deep."""
    assert redact(42) == 42
    assert redact(None) is None


# ── (3) structlog processor strips secrets from event dicts ──
def test_structlog_processor_redacts_event_dict():
    fake = FAKE["openai_key"]
    event = {
        "event": "user asked something",
        "user_id": "abc",
        "raw_text": f"My OpenAI key is {fake}, please remember it",
    }
    out = structlog_redactor(None, "info", event)
    assert fake not in repr(out)
    assert "[REDACTED:openai_key]" in out["raw_text"]


def test_structlog_processor_keeps_safe_values():
    event = {"event": "ok", "n": 5, "user": "alice"}
    out = structlog_redactor(None, "info", event)
    assert out == event


# ── (4) Langfuse client wrapper redacts start_as_current_observation + .update ──
def test_langfuse_wrapper_redacts_start_kwargs_and_span_update():
    from app.infra.tracing import _RedactingLangfuseClient

    fake = FAKE["github_token"]
    captured_start_kwargs: dict = {}
    captured_update_kwargs: dict = {}

    class FakeSpan:
        def update(self, **kw):
            captured_update_kwargs.update(kw)

    class FakeCM:
        def __enter__(self):
            return FakeSpan()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def start_as_current_observation(self, **kw):
            captured_start_kwargs.update(kw)
            return FakeCM()

    wrapped = _RedactingLangfuseClient(FakeClient())
    with wrapped.start_as_current_observation(
        name="chat",
        input=f"user said: {fake}",
        metadata={"raw": fake, "messages": [{"content": fake}]},
    ) as span:
        span.update(output=f"final answer mentions {fake}")

    # Nothing passed to the SDK should contain the raw token.
    assert fake not in repr(captured_start_kwargs)
    assert fake not in repr(captured_update_kwargs)
    assert "[REDACTED:github_token]" in captured_start_kwargs["input"]
    assert "[REDACTED:github_token]" in captured_update_kwargs["output"]


# ── (5) memory.write_memory redacts before persisting ──
def test_write_memory_redacts_summary_and_entities(monkeypatch):
    """End-to-end-ish: mock the repos + model-server, call write_memory,
    assert the persisted summary + entities have no leak."""
    from app.services import memory as memory_service
    from app.repositories import memories as memories_repo
    from app.repositories import audit_log as audit_log_repo

    fake = FAKE["aws_access_key_id"]
    captured: dict = {}

    async def fake_exists(user_id, summary):
        # Capture what `exists` saw too — the idempotency probe also
        # crosses the service boundary.
        captured["exists_summary"] = summary
        return None

    async def fake_insert(**kw):
        captured.update(kw)
        return 1

    async def fake_record(**kw):
        captured["audit_target_id"] = kw.get("target_id")

    async def fake_embed(text):
        captured["embed_input"] = text
        return [0.0] * 768

    monkeypatch.setattr(memories_repo, "exists", fake_exists)
    monkeypatch.setattr(memories_repo, "insert", fake_insert)
    monkeypatch.setattr(audit_log_repo, "record", fake_record)
    monkeypatch.setattr(memory_service.model_server, "embed", fake_embed)

    asyncio.run(memory_service.write_memory(
        user_id="11111111-1111-1111-1111-111111111111",
        conversation_id="conv-1",
        summary=f"User exported AWS creds: {fake}",
        entities=[fake, "innocent_entity"],
    ))

    # Nothing about the AWS key should have made it to the persistence
    # boundary (repo args), the audit, or the embedding call.
    for k in ("exists_summary", "summary", "entities", "embed_input"):
        assert fake not in repr(captured.get(k)), (
            f"AWS key leaked through to memory layer field {k}: {captured.get(k)!r}"
        )
    assert "[REDACTED:aws_access_key_id]" in captured["summary"]
