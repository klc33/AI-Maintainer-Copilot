# app/infra/db.py
"""Back-compat shim. The canonical home for the SQLAlchemy engine,
session factory, and declarative Base is now `app.db.session`. New code
should import from `app.db` directly. Existing imports of the form

    from app.infra.db import engine, async_session_factory, Base

continue to work via the re-exports below.
"""
from app.db.session import (  # noqa: F401
    Base,
    DATABASE_URL,
    async_session_factory,
    engine,
    get_session,
)
