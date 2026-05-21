# app/api/widget.py
"""Public widget endpoints. HTTP-shape only — every piece of logic lives
in `app.services.widget`. The router just translates HTTP <-> service calls
and maps DomainError subclasses to HTTPException."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.domain.exceptions import DomainError
from app.services import widget as widget_service
from app.services.chatbot import stream_chat


router = APIRouter(prefix="/widget", tags=["widget"])

# Sibling router (no /widget prefix) for the public catalog endpoint.
catalog_router = APIRouter(tags=["widget"])


def _raise_for(exc: DomainError) -> None:
    raise HTTPException(exc.status_code, exc.message)


# ── /widgets (catalog) ─────────────────────────────────
@catalog_router.get("/widgets")
async def list_active_widgets():
    return {"widgets": await widget_service.list_active_catalog()}


# ── /widget/{id}/config ────────────────────────────────
@router.get("/{widget_id}/config")
async def get_widget_config(widget_id: str):
    try:
        cfg = await widget_service.get_public_config(widget_id)
    except DomainError as e:
        _raise_for(e)
    return JSONResponse(content=cfg, headers={"Cache-Control": "public, max-age=60"})


# ── /widget/{id}/session ───────────────────────────────
@router.get("/{widget_id}/session")
async def get_widget_session(widget_id: str):
    try:
        return await widget_service.mint_session_token(widget_id)
    except DomainError as e:
        _raise_for(e)


# ── /widget/{id}/embed ─────────────────────────────────
@router.get("/{widget_id}/embed", response_class=HTMLResponse)
async def embed_widget(widget_id: str):
    html, headers = await widget_service.render_embed_page(widget_id)
    return HTMLResponse(html, status_code=200, headers=headers)


# ── /widget/chat ───────────────────────────────────────
@router.post("/chat")
async def widget_chat(request: Request):
    """Streaming chat for anonymous widget sessions. JWT validation goes
    through the widget service; the chatbot service owns Langfuse tracing,
    history, and tool execution."""
    try:
        payload = widget_service.decode_session_token(request.headers.get("Authorization", ""))
    except DomainError as e:
        _raise_for(e)

    session_uuid = payload["sub"][len(widget_service.WIDGET_SESSION_PREFIX):]
    enabled_tools = payload.get("enabled_tools") or []
    widget_id = payload.get("widget_id", "")

    data = await request.json()
    return StreamingResponse(
        stream_chat(
            user_message=data["message"],
            user_id=session_uuid,
            conversation_id=data.get("conversation_id", "widget"),
            enabled_tools=enabled_tools,
            trace_name="widget_chat",
            extra_metadata={"channel": "widget", "widget_id": widget_id, "enabled_tools": enabled_tools},
        ),
        media_type="text/event-stream",
    )
