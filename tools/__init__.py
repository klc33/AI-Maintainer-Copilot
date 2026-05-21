# tools/__init__.py
"""Tool schemas exposed to the LLM live in this top-level package.

Currently a single `registry.py` module owns the canonical TOOLS list.
If/when individual tools grow their own dispatch logic, each can become
its own file (tools/classify_issue.py, etc.) and `registry.py` would
just aggregate them. The chatbot service imports TOOLS from here and
filters by `enabled_tools` from the widget config.
"""
from tools.registry import TOOLS, TOOL_NAMES

__all__ = ["TOOLS", "TOOL_NAMES"]
