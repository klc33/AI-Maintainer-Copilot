#!/usr/bin/env bash
# scripts/seed_vault.sh
#
# Re-seed the dev-mode Vault with the three secrets the stack needs at boot:
#   secret/shared/jwt        JWT signing key (auth + widget session tokens)
#   secret/shared/groq       Groq API key   (chatbot LLM + summarizer)
#   secret/shared/langfuse   Langfuse public + secret keys (tracing)
#
# WHY THIS EXISTS:
#   Vault runs in dev mode (see docker-compose.yml) which keeps secrets in
#   MEMORY ONLY. Every time the Vault container restarts — machine reboot,
#   `docker compose restart`, Docker Desktop restart — all secrets vanish and
#   the api fails its boot check with:
#       Failed to read secret at secret/shared/jwt: ... InvalidPath
#   Run this script to put them back.
#
# USAGE:
#   bash scripts/seed_vault.sh
#   then: docker compose up -d api model-server
#
# Reads Groq + Langfuse values from .env. Generates a fresh JWT secret only
# if one isn't already present (so re-running doesn't needlessly log everyone
# out). VAULT_TOKEN defaults to "root" (the dev-mode root token).
set -euo pipefail

# Git Bash on Windows rewrites /-prefixed args into Windows paths; that would
# mangle `secret/shared/jwt` and `http://vault:8200`. Disable it.
export MSYS_NO_PATHCONV=1

# Run from the project root regardless of where the script is invoked.
cd "$(dirname "$0")/.."

ENV_FILE=".env"
VAULT_SVC="vault"
VAULT_ADDR_INTERNAL="http://vault:8200"
VAULT_TOKEN="${VAULT_TOKEN:-root}"

err() { echo "ERROR: $*" >&2; exit 1; }

# Extract a value from .env. Handles `KEY=value`, `KEY = "value"`, surrounding
# quotes, and leading/trailing whitespace.
get_env() {
  grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" 2>/dev/null | head -1 \
    | sed -E "s/^[^=]*=[[:space:]]*//; s/^\"//; s/\"[[:space:]]*\$//; s/[[:space:]]*\$//"
}

vault_exec() {
  docker compose exec -T -e VAULT_TOKEN="$VAULT_TOKEN" "$VAULT_SVC" "$@"
}

# ── Preconditions ──────────────────────────────────────
[ -f "$ENV_FILE" ] || err ".env not found at $(pwd)/$ENV_FILE"

if ! vault_exec vault status -address="$VAULT_ADDR_INTERNAL" >/dev/null 2>&1; then
  err "Vault is not reachable. Bring it up first:  docker compose up -d vault"
fi

echo "Seeding Vault at $VAULT_ADDR_INTERNAL ..."
echo ""

# ── 1. JWT signing key ─────────────────────────────────
# Only generate if missing — re-running shouldn't rotate the key and log
# every user + widget session out for no reason.
if vault_exec vault kv get -address="$VAULT_ADDR_INTERNAL" secret/shared/jwt >/dev/null 2>&1; then
  echo "  secret/shared/jwt        already present  — left as-is"
else
  JWT_SECRET="$(
    openssl rand -hex 32 2>/dev/null \
      || python  -c 'import secrets;print(secrets.token_hex(32))' 2>/dev/null \
      || python3 -c 'import secrets;print(secrets.token_hex(32))'
  )"
  [ -n "$JWT_SECRET" ] || err "could not generate a JWT secret (need openssl or python)"
  vault_exec vault kv put -address="$VAULT_ADDR_INTERNAL" secret/shared/jwt \
    secret="$JWT_SECRET" >/dev/null
  echo "  secret/shared/jwt        seeded           (freshly generated)"
fi

# ── 2. Groq API key ────────────────────────────────────
GROQ_KEY="$(get_env GROQ_API_KEY)"
[ -n "$GROQ_KEY" ] || err "GROQ_API_KEY not found in $ENV_FILE"
vault_exec vault kv put -address="$VAULT_ADDR_INTERNAL" secret/shared/groq \
  secret="$GROQ_KEY" >/dev/null
echo "  secret/shared/groq       seeded           (from .env)"

# ── 3. Langfuse keys ───────────────────────────────────
# The api's check_langfuse_keys() boot check is mandatory, so both keys
# must be present — a missing one is a hard error, not a skip.
LF_PUBLIC="$(get_env LANGFUSE_PUBLIC_KEY)"
LF_SECRET="$(get_env LANGFUSE_SECRET_KEY)"
[ -n "$LF_PUBLIC" ] || err "LANGFUSE_PUBLIC_KEY not found in $ENV_FILE"
[ -n "$LF_SECRET" ] || err "LANGFUSE_SECRET_KEY not found in $ENV_FILE"
vault_exec vault kv put -address="$VAULT_ADDR_INTERNAL" secret/shared/langfuse \
  public="$LF_PUBLIC" secret="$LF_SECRET" >/dev/null
echo "  secret/shared/langfuse   seeded           (from .env)"

# ── Verify ─────────────────────────────────────────────
echo ""
echo "Secrets now under secret/shared:"
vault_exec vault kv list -address="$VAULT_ADDR_INTERNAL" secret/shared | sed 's/^/  /'

echo ""
echo "Done. Restart the services that read Vault at boot:"
echo "  docker compose up -d api model-server"
