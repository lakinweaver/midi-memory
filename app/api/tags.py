"""Tag vocabulary: what's available to filter by, and renaming across sessions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/tags", tags=["tags"])


class TagRename(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@router.get("")
async def list_tags(request: Request) -> dict:
    return {"tags": request.app.state.db.list_tags()}


@router.patch("/{tag}")
async def rename_tag(request: Request, tag: str, payload: TagRename) -> dict:
    if not request.app.state.db.rename_tag(tag, payload.name):
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"tags": request.app.state.db.list_tags()}
