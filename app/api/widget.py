# app/api/widget.py
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import uuid
from datetime import datetime, timedelta
from jose import jwt
import app.services.auth as auth_mod

router = APIRouter(prefix="/widget", tags=["widget"])

@router.get("/{widget_id}/session")
async def get_widget_session(widget_id: str):
    """Mint a short-lived anonymous JWT for a widget."""
    if not auth_mod.JWT_SECRET:
        return {"error": "JWT secret not loaded"}
    token_data = {
        "sub": f"widget_session:{uuid.uuid4()}",
        "widget_id": widget_id,
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    token = jwt.encode(token_data, auth_mod.JWT_SECRET, algorithm="HS256")
    return {"access_token": token}

@router.get("/{widget_id}/embed", response_class=HTMLResponse)
async def embed_widget(widget_id: str):
    """Return the HTML page that loads the widget bundle."""
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body>
        <div id="mc-widget-root"></div>
        <script src="/widget.js"></script>
        <script>
            fetch('/widget/{widget_id}/session')
                .then(r => r.json())
                .then(d => window.__MC_SESSION_TOKEN__ = d.access_token);
        </script>
    </body>
    </html>
    """, headers={"Content-Security-Policy": "frame-ancestors 'self' *"})