"""Unit tests for the LLM tool registry (tools/registry.py).

The registry is plain data, but it's a contract: widget configs reference
tools by name in their `enabled_tools` allowlist, and the chatbot filters
the schema list against that. A malformed schema or a renamed tool breaks
function-calling silently — these tests fail loudly instead.
"""
from __future__ import annotations

from tools import TOOL_NAMES, TOOLS

# The tools the chatbot dispatches in app/services/chatbot.py::execute_tool.
EXPECTED_TOOLS = {
    "classify_issue",
    "extract_entities",
    "summarize_thread",
    "search_knowledge",
    "write_memory",
}


def test_every_tool_has_function_calling_shape():
    for tool in TOOLS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict) and params["properties"]


def test_required_params_are_declared_in_properties():
    for tool in TOOLS:
        fn = tool["function"]
        props = fn["parameters"]["properties"]
        for required in fn["parameters"].get("required", []):
            assert required in props, (
                f"{fn['name']}: required param '{required}' not in properties"
            )


def test_tool_names_are_unique_and_match_tool_names_export():
    names = [t["function"]["name"] for t in TOOLS]
    assert len(names) == len(set(names)), "duplicate tool name in registry"
    assert TOOL_NAMES == names, "TOOL_NAMES is out of sync with TOOLS"


def test_expected_tool_set_is_present():
    assert set(TOOL_NAMES) == EXPECTED_TOOLS
