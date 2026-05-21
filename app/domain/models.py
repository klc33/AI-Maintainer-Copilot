# app/domain/models.py
"""Back-compat shim. ORM models now live in `app.db.models`. Existing
imports like `from app.domain.models import User` keep working via this
re-export so we don't have to touch every caller (fastapi-users wiring,
the chat / memory routers, etc.) right now.

Migrate callers to `from app.db.models import User` at your leisure."""
from app.db.models import (  # noqa: F401
    AuditLog,
    Chunk,
    Memory,
    User,
    WidgetConfig,
)
