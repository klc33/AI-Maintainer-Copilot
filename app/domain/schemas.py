# app/domain/schemas.py
import uuid
from fastapi_users import schemas
from pydantic import EmailStr

class UserRead(schemas.BaseUser[uuid.UUID]):
    role: str = "user"

class UserCreate(schemas.BaseUserCreate):
    email: EmailStr
    password: str
    role: str = "user"

class UserUpdate(schemas.BaseUserUpdate):
    password: str | None = None
    email: EmailStr | None = None
    role: str | None = None