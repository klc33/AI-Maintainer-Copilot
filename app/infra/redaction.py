# app/infra/redaction.py
"""Secret-scrub layer. Anything that leaves the service boundary —
log lines, Langfuse trace spans, memory rows — passes through `redact()`
or `redact_deep()` first.

Why patterns and not entropy-based detection? Patterns are deterministic,
explainable, and cheap to test. False negatives are caught in code review
by adding a new pattern; false positives reveal themselves quickly and we
tighten the regex. An entropy detector would catch more but produce hard-
to-explain hits and is overkill for the threat model.

See SECURITY.md for the threat model and the per-pattern justification.
"""
from __future__ import annotations

import re
from typing import Any


# ── Pattern catalog ────────────────────────────────────
# Each pattern's compiled regex is matched; matches get replaced with
# `[REDACTED:<name>]`. Order matters: more specific patterns first, so
# (for example) a GitHub fine-grained PAT isn't partially eaten by the
# generic Bearer-header pattern.
PATTERNS: dict[str, re.Pattern[str]] = {
    # ── GitHub ────────────────────────────────────────
    # Classic PAT + OAuth tokens. Five known prefixes; 36-char body.
    # ghp_ (personal), gho_ (oauth), ghu_ (user-to-server),
    # ghs_ (server-to-server), ghr_ (refresh).
    "github_token":       re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    # Fine-grained PAT, 82 chars after the prefix.
    "github_fine_pat":    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22}_[A-Za-z0-9]{59}\b"),

    # ── OpenAI / Groq / Anthropic / Stripe ────────────
    # anthropic_key is listed BEFORE openai_key on purpose: `sk-ant-…`
    # also satisfies the broader `sk-…` openai regex, so the more specific
    # pattern must run first to get the right [REDACTED:<name>] tag.
    "anthropic_key":      re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
    "openai_key":         re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "groq_key":           re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b"),
    "stripe_live_key":    re.compile(r"\b(?:sk|pk|rk)_live_[A-Za-z0-9]{20,}\b"),
    "stripe_test_key":    re.compile(r"\b(?:sk|pk|rk)_test_[A-Za-z0-9]{20,}\b"),

    # ── AWS ───────────────────────────────────────────
    # Access key ID is the easy one — fixed prefix + length, low FP rate.
    "aws_access_key_id":  re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Session tokens are long base64; we don't try (too many FPs against
    # real text). Same for secret access keys (40 char base64). The
    # access-key-id is enough to identify a leak and trigger rotation.

    # ── GCP ───────────────────────────────────────────
    # Service-account email — useful proxy for "someone pasted GCP creds".
    "gcp_sa_email":       re.compile(r"\b[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com\b"),
    # Newer GCP API keys.
    "gcp_api_key":        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),

    # ── Slack ─────────────────────────────────────────
    "slack_token":        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),

    # ── PEM-encoded private keys ──────────────────────
    # Anything starting with -----BEGIN ... PRIVATE KEY----- is a leak
    # regardless of what's inside. We redact the entire block.
    "private_key_block":  re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        re.MULTILINE,
    ),

    # ── JWT-shaped tokens ─────────────────────────────
    # Three base64url-encoded segments separated by dots; first segment
    # starts with `eyJ` (the b64 of `{"`). At least 16 chars per segment
    # to avoid matching short tokens-by-accident in code samples.
    "jwt_token":          re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),

    # ── URLs with embedded user:password ──────────────
    # https://user:pass@host/path → the credential half is the secret.
    "url_basic_auth":     re.compile(r"\b(https?|ftp)://[^\s:@/]+:[^\s:@/]+@[A-Za-z0-9.-]+"),

    # ── HTTP Authorization headers in pasted curl output ──
    # "Authorization: Bearer eyJ..." or "Authorization: token ghp_..."
    "auth_header":        re.compile(
        r"\b[Aa]uthorization:\s*(?:Bearer|Token|Basic|API-Key)\s+[A-Za-z0-9._\-+/=]+",
    ),
}

# We deliberately do NOT redact:
#   - 12-digit AWS account IDs (too many false positives against issue
#     numbers, dates, line counts).
#   - Raw IP / IPv6 addresses (Terraform issue text is full of cluster
#     IPs, RFC1918 ranges, etc.; redacting them all hides triage signal).
#   - Email addresses (PII, but Terraform issue submitters often quote
#     their own emails on purpose; org-level filtering is the right
#     control, not a regex here).
#   - Generic 32-char hex strings (false positives are rampant — every
#     SHA-256 in a stack trace would match).
# See SECURITY.md for the full discussion.


# ── Public API ─────────────────────────────────────────
def redact(text: str) -> str:
    """Redact every known pattern in `text`. Returns the input unchanged
    if it isn't a string (so this is safe to call on heterogeneous values
    via `redact_deep`)."""
    if not isinstance(text, str):
        return text
    for name, pattern in PATTERNS.items():
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


def redact_deep(value: Any) -> Any:
    """Recursive redaction over arbitrarily nested dicts / lists / tuples /
    strings. Non-string scalars pass through unchanged. Used to scrub
    Langfuse span payloads (which can be lists of {role, content} dicts)
    and structlog event dicts."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_deep(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_deep(v) for v in value)
    return value


def structlog_redactor(logger, method_name, event_dict):
    """structlog processor — runs on every log call before rendering.
    Configured in app/main.py."""
    return redact_deep(event_dict)
