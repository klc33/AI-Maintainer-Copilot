# app/services/chatbot.py
"""Chatbot orchestrator with tool‑calling, short‑term memory (Redis),
   episodic long‑term memory (pgvector), and Langfuse v4 tracing."""
import os
import json
from groq import Groq
from app.infra.redis import redis_client
from app.services.memory import write_memory, recall_memories
from app.infra.tracing import get_langfuse_client

# ── Lazy Groq client ──────────────────────────────────
_groq_client = None

def get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client
# ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a maintainer's assistant for a Terraform project.
You have access to tools to classify issues, extract code entities, summarize threads,
search the project's documentation and resolved issues, and store memories.
Always use the appropriate tool when needed, but reply naturally in conversation."""

TOOLS = [
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

async def execute_tool(name: str, args: dict, user_id: str, conversation_id: str) -> dict:
    """Execute a tool call and return a result dict, with Langfuse observation."""
    lf_client = get_langfuse_client()
    with lf_client.start_as_current_observation(
        as_type="span",
        name=f"tool:{name}",
        input=args,
    ) as span:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                if name == "classify_issue":
                    resp = await http_client.post(
                        "http://model-server:8001/classify", json={"text": args["text"]}
                    )
                    result = resp.json() if resp.status_code == 200 else {"error": "classifier unavailable"}
                elif name == "extract_entities":
                    resp = await http_client.post(
                        "http://model-server:8001/extract", json={"text": args["text"]}
                    )
                    result = resp.json() if resp.status_code == 200 else {"error": "NER unavailable"}
                elif name == "summarize_thread":
                    resp = await http_client.post(
                        "http://model-server:8001/summarize", json={"text": args["text"]}
                    )
                    result = resp.json() if resp.status_code == 200 else {"error": "summarizer unavailable"}
                elif name == "search_knowledge":
                    params = {"query": args["query"]}
                    if args.get("content_type"):
                        params["content_type"] = args["content_type"]
                    resp = await http_client.get(
                        "http://model-server:8001/rag/search", params=params
                    )
                    result = resp.json() if resp.status_code == 200 else {"error": "RAG search unavailable"}
                elif name == "write_memory":
                    await write_memory(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        summary=args["summary"],
                        entities=args.get("entities"),
                    )
                    result = {"ok": True, "summary": args["summary"]}
                else:
                    result = {"error": f"Unknown tool: {name}"}
                span.update(output=result)
                return result
        except Exception as e:
            error_result = {"error": f"Tool execution failed: {e}"}
            span.update(output=error_result)
            return error_result

async def stream_chat(user_message: str, user_id: str, conversation_id: str):
    """Generator yielding SSE events for a single chat turn, with Langfuse v4 tracing."""
    groq_client = get_groq_client()
    lf_client = get_langfuse_client()

    # ── Recall long‑term memories (observation) ───────
    with lf_client.start_as_current_observation(
        as_type="span",
        name="recall_memories",
        input=user_message,
    ) as mem_span:
        recalled = await recall_memories(user_id, user_message, top_k=5)
        mem_span.update(output=len(recalled))

    memory_context = ""
    if recalled:
        lines = ["[Relevant past memories:]"]
        for mem in recalled:
            lines.append(f"- {mem['summary']}")
        memory_context = "\n".join(lines)

    system_prompt = SYSTEM_PROMPT + "\n\n" + memory_context if memory_context else SYSTEM_PROMPT

    # ── Conversation history (short‑term) ─────────────
    history_key = f"conv:{conversation_id}:msgs"
    history = await redis_client.lrange(history_key, 0, -1)
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append(json.loads(msg))
    messages.append({"role": "user", "content": user_message})

    # ── First Groq call (tool‑calling, non‑streaming) ──
    with lf_client.start_as_current_observation(
        as_type="generation",
        name="llm_call",
        input=messages,
        model="llama-3.3-70b-versatile",
    ) as llm_span:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=1024,
            stream=False,
        )
        llm_span.update(output=response.choices[0].message.model_dump())

    msg = response.choices[0].message
    if msg.tool_calls:
        for tool_call in msg.tool_calls:
            yield f"data: {json.dumps({'type': 'tool_call_start', 'name': tool_call.function.name, 'args': tool_call.function.arguments})}\n\n"
            tool_result = await execute_tool(
                tool_call.function.name,
                json.loads(tool_call.function.arguments),
                user_id,
                conversation_id,
            )
            yield f"data: {json.dumps({'type': 'tool_call_result', 'name': tool_call.function.name, 'result': tool_result})}\n\n"
            messages.append({
                "role": "assistant",
                "tool_calls": [tool_call.dict()],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result),
            })

        # ── Second call (streaming final answer) ───────
        with lf_client.start_as_current_observation(
            as_type="generation",
            name="llm_final_answer",
            model="llama-3.3-70b-versatile",
        ) as final_span:
            response2 = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.2,
                max_tokens=1024,
                stream=True,
            )
            full_answer = ""
            for chunk in response2:
                if chunk.choices[0].delta.content:
                    full_answer += chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk.choices[0].delta.content})}\n\n"
            final_span.update(output=full_answer)
    else:
        # ── No tool calls – stream answer directly ─────
        with lf_client.start_as_current_observation(
            as_type="generation",
            name="llm_final_answer",
            model="llama-3.3-70b-versatile",
        ) as final_span:
            full_answer = ""
            for chunk in groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.2,
                max_tokens=1024,
                stream=True,
            ):
                if chunk.choices[0].delta.content:
                    full_answer += chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk.choices[0].delta.content})}\n\n"
            final_span.update(output=full_answer)

    yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # ── Store turn in Redis (short‑term) ──────────────
    await redis_client.rpush(history_key, json.dumps({"role": "user", "content": user_message}))
    await redis_client.ltrim(history_key, -20, -1)
    await redis_client.expire(history_key, 86400)