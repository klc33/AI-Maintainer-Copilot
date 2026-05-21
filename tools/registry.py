# tools/registry.py
"""Tool schemas (Groq / OpenAI function-calling shape) the chatbot
exposes to the LLM. The dispatch / implementation for each tool lives in
`app/services/chatbot.py::execute_tool` — the chatbot reads the schemas
from here and the actual side-effects from there.

Keep the `name` field stable: widget configs reference tools by name in
their `enabled_tools` array, and the chatbot filters the schema list
against that allowlist before each turn.
"""
from __future__ import annotations


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "classify_issue",
            "description": "Classify an issue into bug, feature, docs, or question.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_entities",
            "description": "Extract code-shaped entities from text.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_thread",
            "description": "Summarize a long issue thread into WHAT/ASK/STATE.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search documentation and resolved issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "content_type": {"type": "string", "enum": ["docs", "issue", "all"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "Store a fact for future recall.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary"],
            },
        },
    },
]

TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOLS]
