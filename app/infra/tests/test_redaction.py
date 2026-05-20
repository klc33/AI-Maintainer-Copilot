import structlog
from app.infra.redaction import redact

def test_redaction_hides_github_token():
    text = "My token is ghp_abc123def456ghi789jkl012mno345pqr678stu"
    redacted = redact(text)
    assert "ghp_abc123def456ghi789jkl012mno345pqr678stu" not in redacted
    assert "[REDACTED:github_token]" in redacted