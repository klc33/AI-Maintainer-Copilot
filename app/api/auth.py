# app/api/auth.py
from fastapi import APIRouter
from app.services.auth import fastapi_users, auth_backend
from app.domain.schemas import UserRead, UserCreate, UserUpdate

router = APIRouter(prefix="/auth", tags=["auth"])

# Login / logout
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/jwt",
)

# Registration
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
)

# User management (GET /me, PATCH /me, etc.)
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
)