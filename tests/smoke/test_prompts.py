"""Smoke tests for the prompt loader (prompts/_registry.py).

Only `system` is loaded by name in application code today
(app/services/chatbot.py). The other .md files in prompts/ are placeholders
— the summarizer and HyDE keep their prompts inline in
model_server/summarizer.py and model_server/rag_retrieval.py — so this tier
guards the loader contract and the one prompt that actually ships, not the
placeholders' contents.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prompts import get_prompt, invalidate_cache

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def test_system_prompt_loads_and_is_non_empty():
    # The chat system prompt is loaded by get_prompt("system") every turn.
    text = get_prompt("system")
    assert isinstance(text, str) and text.strip(), "system prompt is empty"


def test_unknown_prompt_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        get_prompt("definitely_not_a_real_prompt")


@pytest.mark.parametrize(
    "name", sorted(p.stem for p in _PROMPTS_DIR.glob("*.md"))
)
def test_every_prompt_file_is_loadable(name):
    # get_prompt must resolve every .md present without raising. Content may
    # be empty (placeholder) — that's not this test's concern.
    assert isinstance(get_prompt(name), str)


def test_invalidate_cache_is_callable():
    invalidate_cache()  # dev hot-reload hook — must not raise
