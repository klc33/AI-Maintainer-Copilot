# app/depends.py
"""Central FastAPI dependencies. Routers `from app.depends import ...`
instead of defining their own gate functions inline.

Today:
  - current_active_user : the canonical "I'm logged in" dep
  - require_admin       : built on top of current_active_user; 403s non-admins
  - get_db_session      : re-exported from app.db.session so all DI lives here

Add new dependencies here (rate limits, idempotency keys, tenant scoping…)
rather than scattering Depends factories across routers."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.db.session import get_session as get_db_session
from app.domain.models import User
from app.services.auth import fastapi_users


# Reusable Depends-target. Routers do
#     user: User = Depends(current_active_user)
# rather than calling fastapi_users.current_user(active=True) themselves.
current_active_user = fastapi_users.current_user(active=True)


async def require_admin(user: User = Depends(current_active_user)) -> User:
    """403 for anyone who isn't a superuser or `role='admin'`.
    Use as `user: User = Depends(require_admin)` on every admin endpoint."""
    if not (getattr(user, "is_superuser", False) or getattr(user, "role", "") == "admin"):
        raise HTTPException(403, "admin only")
    return user


__all__ = ["current_active_user", "require_admin", "get_db_session"]
