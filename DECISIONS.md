docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/langfuse public="pk-lf-ee4bae61-147e-47db-b01d-00de5b4c5e9a" secret="sk-lf-aace6c03-14c8-42a2-bde4-937c57066328" this is needed for tracing



# === Seed all Vault secrets for Maintainer's Copilot ===
# Replace the placeholder values with your real keys before running.

# Ensure Vault is healthy
Write-Host "Checking Vault health..." -ForegroundColor Cyan
docker compose exec -T vault vault status -address=http://vault:8200 | Select-String "Sealed" 

# 1. JWT signing key (used for auth and widget tokens)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/jwt secret="a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0"

# 2. Groq API key (for the chatbot LLM)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/groq secret="gsk_your_real_groq_key_here"

# 3. GitHub token (for dataset fetching)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/github token="ghp_your_real_github_token_here"

# 4. Langfuse credentials (optional – only if you want tracing)
docker compose exec -T -e VAULT_TOKEN=root vault vault kv put -address=http://vault:8200 secret/shared/langfuse public="pk-lf-your-public-key" secret="sk-lf-your-secret-key"

Write-Host "All secrets stored in Vault." -ForegroundColor Green