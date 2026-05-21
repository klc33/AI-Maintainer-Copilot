# app/api/__init__.py
"""All FastAPI routers live here. `app.main` imports them from this package
and wires them onto the app instance.

If you add a new router, add a `from .your_router import router as ...`
line here and an `app.include_router(...)` line in `app.main`."""
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.memory import router as memory_router
from app.api.widget import (
    router as widget_router,
    catalog_router as widget_catalog_router,
)
from app.api.widget_admin import router as widget_admin_router

__all__ = [
    "auth_router",
    "chat_router",
    "memory_router",
    "widget_router",
    "widget_catalog_router",
    "widget_admin_router",
]
