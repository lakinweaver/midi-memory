"""The client's small JSON API: status, settings, and a connection test."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["client"])


class SettingsUpdate(BaseModel):
    """Bounds here are the guard rails, not preferences.

    An idle timeout under a couple of seconds would chop a single phrase into
    fragments; a huge one would glue a whole evening into one file.
    """

    idle_seconds: Optional[float] = Field(default=None, ge=3, le=3600)
    min_notes: Optional[int] = Field(default=None, ge=0, le=200)
    min_seconds: Optional[float] = Field(default=None, ge=0, le=120)
    device_match: Optional[str] = Field(default=None, max_length=100)
    server_url: Optional[str] = Field(default=None, max_length=300)
    client_secret: Optional[str] = Field(default=None, max_length=300)
    client_name: Optional[str] = Field(default=None, max_length=40)


def _payload(request: Request) -> dict:
    app = request.app
    store = app.state.settings_store
    return {
        "settings": store.current(),
        "read_only": store.read_only(),
        "status": app.state.service.status(),
        "link": app.state.uploader.state.as_dict(app.state.spool.pending_count()),
    }


@router.get("/status")
async def get_status(request: Request) -> dict:
    return _payload(request)


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    return _payload(request)


@router.put("/settings")
async def update_settings(request: Request, payload: SettingsUpdate) -> dict:
    changes = payload.model_dump(exclude_none=True)
    for key in ("device_match", "server_url", "client_secret", "client_name"):
        if key in changes:
            changes[key] = changes[key].strip()
    # An empty secret field means "leave the one I already have alone", not
    # "forget it" -- the page cannot show the current value to re-submit.
    if not changes.get("client_secret"):
        changes.pop("client_secret", None)
    if "server_url" in changes:
        changes["server_url"] = changes["server_url"].rstrip("/")
    request.app.state.settings_store.save(changes)
    # A newly filled-in link should start draining the spool immediately.
    request.app.state.uploader.nudge()
    return _payload(request)


@router.post("/test-connection")
async def test_connection(request: Request) -> dict:
    return await request.app.state.uploader.hello()


@router.post("/upload-now")
async def upload_now(request: Request) -> dict:
    """Drain the spool on demand, so the page can show the result immediately."""
    sent = await request.app.state.uploader.drain()
    return {"uploaded": sent, **_payload(request)}
