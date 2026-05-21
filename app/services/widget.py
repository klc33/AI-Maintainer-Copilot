# app/services/widget.py
"""Widget business logic — sits between the routers and the
widget_configs repository.

Public concerns (the demo host's end users hit these):
  - resolve a widget by id (404 if missing or inactive)
  - hand back the *safe* fields only (no allowed_origins)
  - mint a short-lived session JWT with the per-widget `enabled_tools`
  - render the embed HTML page with the right CSP frame-ancestors

Admin concerns:
  - thin pass-through CRUD on the repo (the repo is already async-safe).
    Keeping a service layer here means the routers can stay HTTP-only and
    we have a single chokepoint for future business rules (e.g. auditing
    widget_config changes — open issue).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError

import app.services.auth as auth_mod
from app.domain.exceptions import DomainError
from app.repositories import widget_configs as widget_configs_repo


WIDGET_SESSION_PREFIX = "widget_session:"
SESSION_TTL_HOURS = 1

# Where the browser fetches the actual widget bundle. /widget.js on the
# widget nginx is the LOADER stub; the real Vite bundle lives at
# /widget/widget.js. Overridable so we don't hard-code localhost forever.
WIDGET_BUNDLE_URL = os.environ.get(
    "WIDGET_BUNDLE_URL", "http://localhost:8080/widget/widget.js"
)


class WidgetNotFound(DomainError):
    """Raised when a widget_id has no row or the row is inactive."""
    def __init__(self, widget_id: str):
        super().__init__(f"widget '{widget_id}' not found or inactive",
                         code="widget_not_found", status_code=404)


class WidgetAlreadyExists(DomainError):
    def __init__(self, widget_id: str):
        super().__init__(f"widget_id '{widget_id}' already exists",
                         code="widget_id_taken", status_code=409)


# ── Public ─────────────────────────────────────────────
async def get_public_config(widget_id: str) -> dict:
    """For GET /widget/{id}/config. Strips allowed_origins (security filter
    that shouldn't leave the server) and returns only the fields the widget
    JS needs at mount."""
    cfg = await _require_active(widget_id)
    return {
        "widget_id": cfg["widget_id"],
        "name": cfg["name"],
        "theme": cfg["theme"] or {},
    }


async def list_active_catalog() -> list[dict]:
    """For GET /widgets (the demo host's chooser). Public-safe fields only."""
    rows = await widget_configs_repo.list_active()
    return [
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


async def mint_session_token(widget_id: str) -> dict:
    """For GET /widget/{id}/session. Validates the widget is active, then
    mints a short-lived JWT carrying the session UUID and the per-widget
    enabled_tools allowlist."""
    cfg = await _require_active(widget_id)
    if not auth_mod.JWT_SECRET:
        raise DomainError("JWT secret not loaded", code="jwt_not_loaded", status_code=503)
    token_data = {
        "sub": f"{WIDGET_SESSION_PREFIX}{uuid.uuid4()}",
        "widget_id": widget_id,
        "enabled_tools": cfg["enabled_tools"] or [],
        "exp": datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
    }
    token = jwt.encode(token_data, auth_mod.JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "expires_in": SESSION_TTL_HOURS * 3600}


def decode_session_token(authorization_header: str) -> dict:
    """Inverse of mint_session_token. Used by the chat endpoint to validate
    incoming widget JWTs. Returns the decoded payload. Raises DomainError on
    any failure so the router can return a 401 cleanly."""
    if not (authorization_header or "").lower().startswith("bearer "):
        raise DomainError("missing bearer token", code="missing_bearer", status_code=401)
    token = authorization_header.split(" ", 1)[1].strip()
    if not auth_mod.JWT_SECRET:
        raise DomainError("JWT secret not loaded", code="jwt_not_loaded", status_code=503)
    try:
        payload = jwt.decode(token, auth_mod.JWT_SECRET, algorithms=["HS256"])
    except JWTError as e:
        raise DomainError(f"invalid widget token: {e}",
                          code="invalid_widget_token", status_code=401)
    if not (payload.get("sub") or "").startswith(WIDGET_SESSION_PREFIX):
        raise DomainError("not a widget-session token",
                          code="not_widget_token", status_code=401)
    return payload


async def render_embed_page(widget_id: str) -> tuple[str, dict[str, str]]:
    """Returns (html, headers) for GET /widget/{id}/embed.

    If the widget is missing or inactive we return a small "widget
    unavailable" card *inside the iframe* (HTTP 200) instead of a raw
    JSON 404. That makes mistakes look reasonable to whoever's looking
    at the host page."""
    cfg = await widget_configs_repo.get(widget_id)
    if not cfg or not cfg["is_active"]:
        return _NOT_FOUND_HTML.format(widget_id=widget_id), {}

    csp = _frame_ancestors(cfg["allowed_origins"] or [])
    html = _EMBED_TEMPLATE.format(
        title=cfg["name"] or widget_id,
        widget_id=widget_id,
        bundle_url=WIDGET_BUNDLE_URL,
    )
    return html, {"Content-Security-Policy": csp}


# ── Admin pass-through ─────────────────────────────────
async def list_all() -> list[dict]:
    return await widget_configs_repo.list_all()


async def get(widget_id: str) -> dict | None:
    return await widget_configs_repo.get(widget_id)


async def create(
    *,
    widget_id: str,
    name: str,
    description: str,
    allowed_origins: list[str],
    theme: dict,
    enabled_tools: list[str],
    is_active: bool,
) -> dict:
    if await widget_configs_repo.get(widget_id):
        raise WidgetAlreadyExists(widget_id)
    return await widget_configs_repo.create(
        widget_id=widget_id,
        name=name,
        description=description,
        allowed_origins=allowed_origins,
        theme=theme,
        enabled_tools=enabled_tools,
        is_active=is_active,
    )


async def update(
    widget_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    allowed_origins: list[str] | None = None,
    theme: dict | None = None,
    enabled_tools: list[str] | None = None,
    is_active: bool | None = None,
) -> dict:
    cfg = await widget_configs_repo.update(
        widget_id,
        name=name,
        description=description,
        allowed_origins=allowed_origins,
        theme=theme,
        enabled_tools=enabled_tools,
        is_active=is_active,
    )
    if not cfg:
        raise WidgetNotFound(widget_id)
    return cfg


async def delete(widget_id: str) -> None:
    ok = await widget_configs_repo.delete(widget_id)
    if not ok:
        raise WidgetNotFound(widget_id)


# ── Internals ──────────────────────────────────────────
async def _require_active(widget_id: str) -> dict:
    cfg = await widget_configs_repo.get(widget_id)
    if not cfg or not cfg["is_active"]:
        raise WidgetNotFound(widget_id)
    return cfg


def _frame_ancestors(allowed_origins: list[str]) -> str:
    if not allowed_origins:
        return "frame-ancestors 'none'"
    return "frame-ancestors " + " ".join(allowed_origins)


# ── Templates ──────────────────────────────────────────
_EMBED_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
</head>
<body style="margin:0;background:transparent;">
  <div id="mc-widget-root"></div>
  <script>
    window.__MC_WIDGET_ID__ = "{widget_id}";
    Promise.all([
      fetch('/widget/{widget_id}/config').then(r => r.json()),
      fetch('/widget/{widget_id}/session').then(r => r.json())
    ]).then(([cfg, sess]) => {{
      window.__MC_CONFIG__ = cfg;
      window.__MC_SESSION_TOKEN__ = sess.access_token;
      window.dispatchEvent(new CustomEvent('mc:ready'));
    }}).catch(err => console.error('widget bootstrap failed', err));
  </script>
  <script src="{bundle_url}"></script>
</body>
</html>"""


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
