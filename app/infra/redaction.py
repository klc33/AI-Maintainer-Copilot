# app/infra/redaction.py
import re

PATTERNS = {
    "github_token": re.compile(r"ghp_[a-zA-Z0-9]{36}"),
    "openai_key": re.compile(r"sk-[a-zA-Z0-9]{32}"),
    # add others as needed
}

def redact(text: str) -> str:
    for name, pattern in PATTERNS.items():
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text