# app/api/memory.py
from fastapi import APIRouter, Depends
from app.services.auth import fastapi_users
from app.domain.models import User
from app.services.memory import list_memories

router = APIRouter(prefix="/memory", tags=["memory"])

@router.get("/list")
async def get_memories(
    user: User = Depends(fastapi_users.current_user(active=True)),
):
    """Return all episodic memories for the current user."""
    memories = await list_memories(str(user.id))
    return {"memories": memories}