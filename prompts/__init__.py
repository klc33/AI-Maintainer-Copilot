# prompts/__init__.py
"""Re-export the loader so callers can just `from prompts import get_prompt`."""
from prompts._registry import get_prompt, invalidate_cache

__all__ = ["get_prompt", "invalidate_cache"]
