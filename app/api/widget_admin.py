# app/api/widget_admin.py
"""Admin-only CRUD for widget configs. HTTP-shape only — all logic is in
`app.services.widget`. Required role: admin (or is_superuser)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.depends import require_admin
from app.domain.exceptions import DomainError
from app.domain.models import User
from app.services import widget as widget_service


router = APIRouter(prefix="/admin/widgets", tags=["admin"])


def _raise_for(exc: DomainError) -> None:
    raise HTTPException(exc.status_code, exc.message)


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
async def list_widgets(_: User = Depends(require_admin)):
    return {"widgets": await widget_service.list_all()}


@router.get("/{widget_id}")
async def get_widget(widget_id: str, _: User = Depends(require_admin)):
    cfg = await widget_service.get(widget_id)
    if not cfg:
        raise HTTPException(404, "widget not found")
    return cfg


@router.post("", status_code=201)
async def create_widget(body: WidgetConfigIn, _: User = Depends(require_admin)):
    try:
        return await widget_service.create(
            widget_id=body.widget_id,
            name=body.name,
            description=body.description,
            allowed_origins=body.allowed_origins,
            theme=body.theme,
            enabled_tools=body.enabled_tools,
            is_active=body.is_active,
        )
    except DomainError as e:
        _raise_for(e)


@router.patch("/{widget_id}")
async def update_widget(widget_id: str, body: WidgetConfigPatch, _: User = Depends(require_admin)):
    try:
        return await widget_service.update(
            widget_id,
            name=body.name,
            description=body.description,
            allowed_origins=body.allowed_origins,
            theme=body.theme,
            enabled_tools=body.enabled_tools,
            is_active=body.is_active,
        )
    except DomainError as e:
        _raise_for(e)


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(widget_id: str, _: User = Depends(require_admin)):
    try:
        await widget_service.delete(widget_id)
    except DomainError as e:
        _raise_for(e)
    return None
