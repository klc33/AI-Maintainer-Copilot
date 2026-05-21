# app/api/widget_admin.py
"""Admin-only CRUD for widget configs. Required role: admin."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.auth import fastapi_users
from app.domain.models import User
from app.repositories import widget_configs

router = APIRouter(prefix="/admin/widgets", tags=["admin"])


def _require_admin(user: User = Depends(fastapi_users.current_user(active=True))) -> User:
    """Allow superusers OR users whose role column is 'admin'."""
    if not (getattr(user, "is_superuser", False) or getattr(user, "role", "") == "admin"):
        raise HTTPException(403, "admin only")
    return user


class WidgetConfigIn(BaseModel):
    widget_id: str = Field(..., min_length=1, max_length=64)
    name: str = ""
    description: str = ""
    allowed_origins: list[str] = []
    theme: dict = Field(default_factory=dict)
    enabled_tools: list[str] = []
    is_active: bool = True


class WidgetConfigPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    allowed_origins: list[str] | None = None
    theme: dict | None = None
    enabled_tools: list[str] | None = None
    is_active: bool | None = None


@router.get("")
async def list_widgets(_: User = Depends(_require_admin)):
    return {"widgets": await widget_configs.list_all()}


@router.get("/{widget_id}")
async def get_widget(widget_id: str, _: User = Depends(_require_admin)):
    cfg = await widget_configs.get(widget_id)
    if not cfg:
        raise HTTPException(404, "widget not found")
    return cfg


@router.post("", status_code=201)
async def create_widget(body: WidgetConfigIn, _: User = Depends(_require_admin)):
    if await widget_configs.get(body.widget_id):
        raise HTTPException(409, "widget_id already exists")
    return await widget_configs.create(
        widget_id=body.widget_id,
        name=body.name,
        description=body.description,
        allowed_origins=body.allowed_origins,
        theme=body.theme,
        enabled_tools=body.enabled_tools,
        is_active=body.is_active,
    )


@router.patch("/{widget_id}")
async def update_widget(widget_id: str, body: WidgetConfigPatch, _: User = Depends(_require_admin)):
    cfg = await widget_configs.update(
        widget_id,
        name=body.name,
        description=body.description,
        allowed_origins=body.allowed_origins,
        theme=body.theme,
        enabled_tools=body.enabled_tools,
        is_active=body.is_active,
    )
    if not cfg:
        raise HTTPException(404, "widget not found")
    return cfg


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(widget_id: str, _: User = Depends(_require_admin)):
    ok = await widget_configs.delete(widget_id)
    if not ok:
        raise HTTPException(404, "widget not found")
    return None
