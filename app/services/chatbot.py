# app/services/chatbot.py
"""Chatbot orchestrator with tool‑calling, short‑term memory (Redis),
   episodic long‑term memory (pgvector), and Langfuse v4 tracing.

External integrations go through infra adapters:
  - LLM:           app.infra.llm.get_chat_client()
  - model-server:  app.infra.model_server (classify, extract, summarize, rag_search)
  - tracing:       app.infra.tracing
  - blob storage:  app.infra.blob
  - Redis history: app.repositories.conversation_history (uses app.infra.redis)
"""
import os
import json
import asyncio

from app.infra import blob, model_server
from app.infra.llm import get_chat_client
from app.infra.tracing import get_langfuse_client
from app.repositories import conversation_history as history_repo
from app.services.memory import write_memory, recall_memories
from prompts import get_prompt
from tools import TOOLS

# Spec: "per-conversation retrieved-chunks snapshots for the last N conversations".
# We snapshot every search_knowledge call into MinIO and asynchronously prune
# back to this many distinct conversation_ids on each write.
CONVERSATION_SNAPSHOT_KEEP_N = int(os.environ.get("CONVERSATION_SNAPSHOT_KEEP_N", "100"))

async def execute_tool(name: str, args: dict, user_id: str, conversation_id: str) -> dict:
    """Execute a tool call and return a result dict, with Langfuse observation."""
    lf_client = get_langfuse_client()
    with lf_client.start_as_current_observation(
        as_type="span",
        name=f"tool:{name}",
        input=args,
    ) as span:
        try:
            if name == "classify_issue":
                result = await model_server.classify(args["text"])
            elif name == "extract_entities":
                result = await model_server.extract_entities(args["text"])
            elif name == "summarize_thread":
                result = await model_server.summarize(args["text"])
            elif name == "search_knowledge":
                result = await model_server.rag_search(
                    args["query"], content_type=args.get("content_type")
                )
                # Snapshot retrieved chunks for this conversation (fire-and-forget).
                # Lets us debug "why did the bot say that?" by looking up
                # exactly what RAG handed the LLM in that turn.
                if "error" not in result:
                    snapshot = {
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "timestamp": blob.utc_key_timestamp(),
                        "query": args["query"],
                        "content_type": args.get("content_type"),
                        "retrieved_chunks": result.get("results", []),
                    }
                    key = f"conversations/{conversation_id}/{snapshot['timestamp']}.json"
                    asyncio.create_task(blob.aput_json(blob.BUCKET_CONVERSATIONS, key, snapshot))
                    asyncio.create_task(
                        blob.aprune_keep_newest(
                            blob.BUCKET_CONVERSATIONS, "conversations/", CONVERSATION_SNAPSHOT_KEEP_N
                        )
                    )
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

async def stream_chat(
    user_message: str,
    user_id: str,
    conversation_id: str,
    enabled_tools: list[str] | None = None,
    trace_name: str = "chat",
    extra_metadata: dict | None = None,
):
    """Async generator yielding SSE events for a single chat turn, with
    Langfuse v4 tracing fully owned by the service (the router stays
    HTTP-only and has no Langfuse import).

    Parameters
    ----------
    enabled_tools : list[str] | None
        Restricts which tools the LLM can call. None means "all tools"
        (the authed Streamlit chat path). The widget chat path passes the
        widget config's allowlist.
    trace_name : str
        Top-level span name, e.g. "chat" or "widget_chat".
    extra_metadata : dict | None
        Merged into the root span's metadata. Used by widget callers to
        attach widget_id / channel.
    """
    lf_client = get_langfuse_client()
    root_metadata = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        **(extra_metadata or {}),
    }
    with lf_client.start_as_current_observation(
        as_type="span",
        name=trace_name,
        input=user_message,
        trace_context={"trace_id": lf_client.create_trace_id()},
        metadata=root_metadata,
    ) as root_span:
        async for event in _stream_chat_inner(user_message, user_id, conversation_id, enabled_tools):
            yield event
        root_span.update(output="done")


async def _stream_chat_inner(
    user_message: str,
    user_id: str,
    conversation_id: str,
    enabled_tools: list[str] | None,
):
    """The real chat-turn body. Kept separate from `stream_chat` so the
    outer Langfuse span can wrap it with a plain `with` block (no manual
    __enter__/__exit__) while still letting us yield SSE frames through.
    """
    groq_client = get_chat_client()
    lf_client = get_langfuse_client()

    # Filter the global TOOLS list by name when an allowlist is supplied.
    if enabled_tools is None:
        tools_to_use = TOOLS
    else:
        allowed = set(enabled_tools)
        tools_to_use = [t for t in TOOLS if t["function"]["name"] in allowed]

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

    base_system = get_prompt("system")
    system_prompt = base_system + "\n\n" + memory_context if memory_context else base_system

    # ── Conversation history (short‑term) ─────────────
    history = await history_repo.load(conversation_id)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # ── First Groq call (tool‑calling, non‑streaming) ──
    with lf_client.start_as_current_observation(
        as_type="generation",
        name="llm_call",
        input=messages,
        model="llama-3.3-70b-versatile",
    ) as llm_span:
        # If the allowlist filtered everything out (or was empty), don't send
        # an empty tools array — Groq rejects it. Just skip tool-calling for
        # this turn so the LLM falls through to the plain streaming reply.
        first_call_kwargs = dict(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.2,
            max_tokens=1024,
            stream=False,
        )
        if tools_to_use:
            first_call_kwargs["tools"] = tools_to_use
            first_call_kwargs["tool_choice"] = "auto"
        response = groq_client.chat.completions.create(**first_call_kwargs)
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

    # ── Store turn in short-term history ──────────────
    # Persist BOTH the user message and the assistant's final answer so the
    # LLM doesn't re-process old user requests next turn (the bug behind the
    # original "remember my name" being written 11 times).
    turn = [{"role": "user", "content": user_message}]
    if full_answer:
        turn.append({"role": "assistant", "content": full_answer})
    await history_repo.append(conversation_id, turn)