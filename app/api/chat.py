# app/api/chat.py
"""HTTP shape only. No DB / Redis / external-system imports here —
tracing (Langfuse), the LLM client, and any persistence are all owned by
the chatbot service."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.depends import current_active_user
from app.domain.models import User
from app.services.chatbot import stream_chat


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message")
async def chat_message(
    request: Request,
    user: User = Depends(current_active_user),
):
    data = await request.json()
    return StreamingResponse(
        stream_chat(
            user_message=data["message"],
            user_id=str(user.id),
            conversation_id=data.get("conversation_id", "default"),
            trace_name="chat",
        ),
        media_type="text/event-stream",
    )
