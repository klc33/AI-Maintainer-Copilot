# Security

## Redaction layer

### What it is

`app/infra/redaction.py` is a regex-based scrub that runs on every payload
crossing a service boundary. Three exits, all wired through redaction:

| Exit | Where redaction runs |
|---|---|
| **Logs** (stdout / journald / wherever structlog writes) | `structlog_redactor` is registered as the *first* processor in the structlog chain (see `app/main.py`). Every `logger.info(...)` event dict is `redact_deep`'d before any other processor sees it. |
| **Traces** (Langfuse spans → Langfuse server over HTTPS) | The Langfuse client is wrapped by `_RedactingLangfuseClient` in `app/infra/tracing.py`. `start_as_current_observation(...)` kwargs (`input`, `output`, `metadata`) and `span.update(...)` kwargs are redacted before the SDK ships them. |
| **Memory writes** (Postgres `memories` table + audit log + model-server `/embed` call) | `app/services/memory.py:write_memory` calls `redact()` on `summary` and `redact_deep()` on `entities` as its first operation, before the idempotency probe, the embedding call, or the insert. |

A unit test (`app/infra/tests/test_redaction.py::test_write_memory_redacts_summary_and_entities`) asserts that a message containing a fake AWS access key never reaches any of those three exits in raw form. Parallel tests exist for the structlog processor and the Langfuse wrapper.

### What gets redacted

Each pattern below is a regex in `PATTERNS`. A match becomes
`[REDACTED:<pattern_name>]`. The name is preserved in the placeholder so
SREs can grep "I see github_token leaked? no, I see [REDACTED:github_token],
working as intended."

| Pattern | Regex shape | Why it's in the list |
|---|---|---|
| `github_token` | `gh[pousr]_[A-Za-z0-9]{36,}` | Personal / OAuth / user-to-server / server-to-server / refresh tokens. Users routinely paste `gh-cli` output into issues. |
| `github_fine_pat` | `github_pat_…_…` (22+59 chars) | Newer GitHub fine-grained PAT format. Same justification. |
| `openai_key` | `sk-(proj-)?[A-Za-z0-9_-]{20,}` | Anyone debugging an LLM tool will have one in their shell and may paste a curl trace. Also covers the new `sk-proj-…` project-scoped keys. |
| `groq_key` | `gsk_[A-Za-z0-9]{40,}` | The project itself runs on Groq; cross-contamination from a user's own Groq key into an issue is plausible. |
| `anthropic_key` | `sk-ant-[A-Za-z0-9_-]{40,}` | Same family. |
| `stripe_live_key` | `(sk|pk|rk)_live_[A-Za-z0-9]{20,}` | If a Terraform user is provisioning a billing system, a Stripe key in a heredoc is a real failure mode. |
| `stripe_test_key` | `(sk|pk|rk)_test_[A-Za-z0-9]{20,}` | Less harmful than live, but still belongs to someone — and the cost of redacting it is zero. |
| `aws_access_key_id` | `(AKIA|ASIA)[0-9A-Z]{16}` | Pasted IAM credentials are *the* most likely Terraform leak. We don't try to match the matching *secret* (40 chars of base64 — too many false positives). The access key alone is enough to identify a leak and trigger rotation. |
| `gcp_sa_email` | `…@…iam.gserviceaccount.com` | Service account emails often leak when users paste GCP errors verbatim. Even without the JSON key file attached, the email is a finding. |
| `gcp_api_key` | `AIza[0-9A-Za-z_-]{35}` | GCP's broadly-issued API keys (Maps, Firebase, etc.). |
| `slack_token` | `xox[baprs]-…` | Webhook URLs and bot tokens. |
| `private_key_block` | `-----BEGIN ... PRIVATE KEY-----…-----END …-----` (multi-line) | Anyone pasting a `.pem` for any reason. The regex spans newlines (`re.MULTILINE`) and matches the whole block, so the resulting placeholder doesn't leave key data behind. |
| `jwt_token` | `eyJ[A-Za-z0-9_-]{16,}.[…].[…]` | Anything that looks like a JWT — including the Langfuse keys our own infra emits, ironically, so this also protects against accidental self-leak in dev. |
| `url_basic_auth` | `https?://user:pass@host` | Embedded HTTP basic auth. Common in CI logs (`git push https://x:$TOKEN@github.com/…`). |
| `auth_header` | `Authorization: Bearer …` | Captures a pasted curl trace where the token is split across header + value. The token itself usually also matches one of the prefix-specific patterns above; this is the catch-all for unknown bearer schemes. |

### What deliberately is NOT in the list

I considered and rejected the following — each is documented inline in `redaction.py`:

| Not matched | Why |
|---|---|
| **AWS account IDs** (12-digit numbers in ARNs) | Too many false positives. Issue numbers, dates, file line counts, version codes all match `\d{12}`. The access-key-id pattern is already where the actionable signal lives. |
| **IP addresses / IPv6** | Real Terraform issue text is full of cluster IPs, RFC1918 ranges, load-balancer DNS names. Redacting them all destroys triage signal and the addresses themselves aren't credentials. |
| **Email addresses** | Submitters often quote their own email on purpose, and PII handling for emails belongs at the org/storage layer (DSAR pipeline), not a per-line regex. We do redact `*.iam.gserviceaccount.com` because *those* are credential-shaped. |
| **Generic 32-char hex strings** | Every SHA-256 in a stack trace would match. Catastrophic for issue text. |
| **AWS secret access keys** (40-char base64) | The shape is indistinguishable from any other 40-character base64 string — random JWT segments, opaque cache keys, you name it. The access key ID being redacted is sufficient to identify the leak; the secret itself getting indexed in our logs is bad but rotating on the access-key trigger handles it. |
| **Whole UUIDs** | We use UUIDs for user IDs, conversation IDs, etc. — those are *supposed* to flow through. |

### Threat model

We are defending against:

1. **A user pasting their own credentials** into chat (the `write_memory` tool then trying to persist them — most likely vector).
2. **Our own code accidentally `logger.info(secret_value)`** during a bug investigation.
3. **A trace span shipping the raw chat-turn input** to Langfuse, which is a SaaS we don't control.

We are NOT defending against:

- An attacker with code execution inside the api container exfiltrating memory directly.
- Malicious patterns designed to evade the regex (token in mixed case, base64-of-base64). A regex layer can't beat a determined exfiltrator; that's an *anomaly detection* problem, not a *redaction* problem.
- Side channels — e.g. an attacker correlating embedding vectors to recover the input. Out of scope for a regex.

### How to update

1. Add a new entry to `PATTERNS` in `app/infra/redaction.py`. Order it among more-specific patterns first.
2. Add a fake-key entry to the `FAKE` dict in `app/infra/tests/test_redaction.py`. The parametrized test (`test_every_pattern_is_redacted`) will pick it up automatically.
3. If the new pattern needs justification, add a row to the "What gets redacted" table above.
4. Don't remove patterns without a comment explaining why — *they're cheap*. A pattern that's never matched costs ~one regex compile at boot.
