# prompts/_registry.py
"""Prompt loader. All chat / tool prompts live alongside this file as .md
and are loaded by name, e.g.:

    from prompts import get_prompt
    sys_prompt = get_prompt("system")

Loaded text is cached after first read so we don't hit disk per request.
If you want hot-reload during dev, call invalidate_cache().
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=64)
def get_prompt(name: str) -> str:
    """Return the contents of `prompts/{name}.md` (stripped). Raises
    FileNotFoundError if the prompt doesn't exist — that's a deploy bug,
    not something to silently default away."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt '{name}' not found at {path}")
    return path.read_text(encoding="utf-8").strip()


def invalidate_cache() -> None:
    """Drop the LRU cache so subsequent get_prompt calls re-read disk.
    Useful in dev when editing .md files without restarting the process."""
    get_prompt.cache_clear()
