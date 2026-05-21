# app/api/widget.py
"""Public widget endpoints — embed page, session JWT, runtime config, chat.

This is the only path through the API that does not go through fastapi-users.
Auth for /widget/chat is a session JWT minted by /widget/{id}/session, which
embeds the widget_id and the per-widget enabled_tools list so chat traffic
can be tied back to a specific configured widget."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from jose import jwt, JWTError

import app.services.auth as auth_mod
from app.services.chatbot import stream_chat
from app.infra.tracing import get_langfuse_client
from app.repositories import widget_configs

router = APIRouter(prefix="/widget", tags=["widget"])
# Sibling router (no /widget prefix) for the public catalog endpoint.
catalog_router = APIRouter(tags=["widget"])


@catalog_router.get("/widgets")
async def list_active_widgets():
    """Public catalog of active widgets, used by the demo host chooser.
    Returns only safe fields: widget_id, name, description, theme color
    (for the UI accent), and enabled_tools (shown as capability chips).
    Never returns allowed_origins (security-relevant filter)."""
    rows = await widget_configs.list_active()
    return {
        "widgets": [
            {
                "widget_id": r["widget_id"],
                "name": r["name"],
                "description": r.get("description") or "",
                "color": (r.get("theme") or {}).get("color"),
                "position": (r.get("theme") or {}).get("position"),
                "enabled_tools": r.get("enabled_tools") or [],
            }
            for r in rows
        ]
    }

WIDGET_SESSION_PREFIX = "widget_session:"
SESSION_TTL_HOURS = 1

# Where the browser will fetch the actual widget bundle from. /widget.js on
# the widget nginx is the LOADER stub; the real Vite bundle lives at
# /widget/widget.js. Overridable so we don't have to hard-code localhost.
WIDGET_BUNDLE_URL = os.environ.get(
    "WIDGET_BUNDLE_URL", "http://localhost:8080/widget/widget.js"
)


def _frame_ancestors(allowed_origins: list[str]) -> str:
    """Build the CSP frame-ancestors directive from the widget's allowed
    origins. Empty list -> 'none' (no one can frame us)."""
    if not allowed_origins:
        return "frame-ancestors 'none'"
    return "frame-ancestors " + " ".join(allowed_origins)


def _decode_widget_token(request: Request) -> dict:
    """Validate the Authorization header carries a widget-session JWT we minted.
    Returns the JWT payload (sub, widget_id, enabled_tools, exp)."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    if not auth_mod.JWT_SECRET:
        raise HTTPException(503, "JWT secret not loaded")
    try:
        payload = jwt.decode(token, auth_mod.JWT_SECRET, algorithms=["HS256"])
    except JWTError as e:
        raise HTTPException(401, f"invalid widget token: {e}")
    if not (payload.get("sub") or "").startswith(WIDGET_SESSION_PREFIX):
        raise HTTPException(401, "not a widget-session token")
    return payload


# ── Public: runtime config ─────────────────────────────
@router.get("/{widget_id}/config")
async def get_widget_config(widget_id: str):
    """Public read-only config used by the widget JS at mount time.
    Returns ONLY safe fields — allowed_origins is a security-relevant filter
    and is never exposed to the browser."""
    cfg = await widget_configs.get(widget_id)
    if not cfg or not cfg["is_active"]:
        raise HTTPException(404, "widget not found")
    return JSONResponse(
        content={
            "widget_id": cfg["widget_id"],
            "name": cfg["name"],
            "theme": cfg["theme"] or {},
        },
        headers={"Cache-Control": "public, max-age=60"},
    )


# ── Public: mint a per-session JWT ─────────────────────
@router.get("/{widget_id}/session")
async def get_widget_session(widget_id: str):
    """Mint a short-lived anonymous JWT for a widget session."""
    cfg = await widget_configs.get(widget_id)
    if not cfg or not cfg["is_active"]:
        raise HTTPException(404, "widget not found")
    if not auth_mod.JWT_SECRET:
        raise HTTPException(503, "JWT secret not loaded")
    token_data = {
        "sub": f"{WIDGET_SESSION_PREFIX}{uuid.uuid4()}",
        "widget_id": widget_id,
        "enabled_tools": cfg["enabled_tools"] or [],
        "exp": datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
    }
    token = jwt.encode(token_data, auth_mod.JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "expires_in": SESSION_TTL_HOURS * 3600}


_NOT_FOUND_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Widget unavailable</title>
<style>
  html, body {{ margin: 0; height: 100%; background: transparent; }}
  body {{ display: grid; place-items: end; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  .card {{
    margin: 0 12px 12px 0; padding: 12px 14px; max-width: 280px;
    background: #fff; color: #0f172a; border: 1px solid #e5e7eb;
    border-radius: 14px; box-shadow: 0 8px 20px rgba(15,23,42,.18);
    font-size: 13px; line-height: 1.4;
  }}
  .card strong {{ display: block; margin-bottom: 4px; }}
  .card code {{ background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 12px; }}
</style>
</head><body>
  <div class="card">
    <strong>Widget unavailable</strong>
    No active widget with id <code>{widget_id}</code>. Check the admin panel.
  </div>
</body></html>"""


# ── Public: HTML embed page ────────────────────────────
@router.get("/{widget_id}/embed", response_class=HTMLResponse)
async def embed_widget(widget_id: str):
    """The HTML loaded into the iframe injected by /widget.js. Bootstraps the
    widget by pre-fetching config + session, then dispatches mc:ready so
    widget.jsx can render with the theme already applied.

    If the requested widget_id is missing or inactive we return a small
    styled HTML card *inside* the iframe instead of raw JSON, so the host
    page shows a readable hint instead of `{"detail":"widget not found"}`."""
    cfg = await widget_configs.get(widget_id)
    if not cfg or not cfg["is_active"]:
        # Use 200 so the iframe renders the body; the message itself
        # communicates the failure to whoever's looking at the page.
        return HTMLResponse(
            _NOT_FOUND_HTML.format(widget_id=widget_id),
            status_code=200,
        )
    csp = _frame_ancestors(cfg["allowed_origins"] or [])
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{cfg['name']}</title>
</head>
<body style="margin:0;background:transparent;">
  <div id="mc-widget-root"></div>
  <script>
    window.__MC_WIDGET_ID__ = {widget_id!r};
    Promise.all([
      fetch('/widget/{widget_id}/config').then(r => r.json()),
      fetch('/widget/{widget_id}/session').then(r => r.json())
    ]).then(([cfg, sess]) => {{
      window.__MC_CONFIG__ = cfg;
      window.__MC_SESSION_TOKEN__ = sess.access_token;
      window.dispatchEvent(new CustomEvent('mc:ready'));
    }}).catch(err => console.error('widget bootstrap failed', err));
  </script>
  <script src="{WIDGET_BUNDLE_URL}"></script>
</body>
</html>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


# ── Public: streaming chat ─────────────────────────────
@router.post("/chat")
async def widget_chat(request: Request):
    """Streaming chat for anonymous widget sessions. The session JWT embeds
    the widget_id and enabled_tools so each widget can have its own LLM
    capability scope without re-querying Postgres on every message."""
    payload = _decode_widget_token(request)
    session_uuid = payload["sub"][len(WIDGET_SESSION_PREFIX):]
    enabled_tools = payload.get("enabled_tools") or []

    data = await request.json()
    user_message = data["message"]
    conversation_id = data.get("conversation_id", "widget")
    widget_id = payload.get("widget_id", "")

    client = get_langfuse_client()
    trace_id = client.create_trace_id()

    async def event_stream():
        with client.start_as_current_observation(
            as_type="span",
            name="widget_chat",
            input=user_message,
            trace_context={"trace_id": trace_id},
            metadata={
                "user_id": session_uuid,
                "conversation_id": conversation_id,
                "channel": "widget",
                "widget_id": widget_id,
                "enabled_tools": enabled_tools,
            },
        ) as root_span:
            async for event in stream_chat(
                user_message, session_uuid, conversation_id, enabled_tools=enabled_tools
            ):
                yield event
            root_span.update(output="done")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
