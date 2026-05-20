# app/api/chat.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from app.services.auth import fastapi_users
from app.domain.models import User
from app.services.chatbot import stream_chat
from app.infra.tracing import get_langfuse_client

router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("/message")
async def chat_message(
    request: Request,
    user: User = Depends(fastapi_users.current_user(active=True)),
):
    data = await request.json()
    user_message = data["message"]
    conversation_id = data.get("conversation_id", "default")
    user_id = str(user.id)

    client = get_langfuse_client()
    trace_id = client.create_trace_id()

    async def event_stream():
        with client.start_as_current_observation(
            as_type="span",
            name="chat",
            input=user_message,
            trace_context={"trace_id": trace_id},
            metadata={"user_id": user_id, "conversation_id": conversation_id},
        ) as root_span:
            async for event in stream_chat(user_message, user_id, conversation_id):
                yield event
            root_span.update(output="done")

    return StreamingResponse(event_stream(), media_type="text/event-stream")