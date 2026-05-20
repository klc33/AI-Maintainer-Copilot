# app/services/auth.py
import uuid
from fastapi import Depends
from fastapi_users import FastAPIUsers, BaseUserManager
from fastapi_users.authentication import (
    JWTStrategy,
    AuthenticationBackend,
    BearerTransport,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from app.domain.models import User
from app.infra.db import async_session_factory

# JWT secret is set at startup by the boot check (loaded from Vault)
JWT_SECRET = None

# ── User database adapter ──────────────────────────────
async def get_user_db():
    async with async_session_factory() as session:
        yield SQLAlchemyUserDatabase(session, User)

# ── User manager (needed for fastapi-users >= 14.0) ────
class UserManager(BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = "dummy-reset-secret"
    verification_token_secret = "dummy-verification-secret"

    async def on_after_register(self, user: User, request=None):
        pass

    # This method must be SYNCHRONOUS, not async
    def parse_id(self, value: str) -> uuid.UUID:
        return uuid.UUID(value)

async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)

# ── Transport & strategy ───────────────────────────────
bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")

def get_jwt_strategy():
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET not loaded – boot check must run first")
    return JWTStrategy(secret=JWT_SECRET, lifetime_seconds=3600 * 24)

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

# ── FastAPIUsers instance – now using the UserManager ──
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)