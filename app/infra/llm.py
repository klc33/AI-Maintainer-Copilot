# app/infra/llm.py
"""LLM provider adapter.

Only Groq is wired in today. Keeping the adapter shape (`get_chat_client()`)
means swapping in OpenAI, Anthropic, etc. later is a single-file change —
service code stays put.

The client is lazy because `GROQ_API_KEY` is pulled from Vault at boot
(see app/main.py `check_groq_key`). Instantiating Groq at import time would
fail before the boot check runs.
"""
from __future__ import annotations

import os

from groq import Groq


_client: Groq | None = None


def get_chat_client() -> Groq:
    """Return the singleton chat-completions client. Raises KeyError if
    GROQ_API_KEY isn't in the environment yet — the boot check should have
    loaded it from Vault by the time any request handler runs."""
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client
