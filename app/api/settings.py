"""Read and change the settings that can be adjusted without a restart."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import samples

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """Bounds here are the guard rails, not preferences.

    An idle timeout under a couple of seconds would chop a single phrase into
    fragments; a huge one would glue a whole evening into one file.
    """

    idle_seconds: Optional[float] = Field(default=None, ge=3, le=3600)
    min_notes: Optional[int] = Field(default=None, ge=0, le=200)
    min_seconds: Optional[float] = Field(default=None, ge=0, le=120)
    device_match: Optional[str] = Field(default=None, max_length=100)


def _payload(request: Request) -> dict:
    store = request.app.state.settings_store
    service = request.app.state.service
    return {
        "settings": store.current(),
        "read_only": store.read_only(),
        "input": {"connected": service.source.connected,
                  "port_name": service.source.port_name,
                  "kind": service.source.kind},
        "samples": samples.status(),
    }


@router.get("")
async def get_settings(request: Request) -> dict:
    return _payload(request)


@router.put("")
async def update_settings(request: Request, payload: SettingsUpdate) -> dict:
    changes = payload.model_dump(exclude_none=True)
    if "device_match" in changes:
        changes["device_match"] = changes["device_match"].strip()
    request.app.state.settings_store.save(changes)
    return _payload(request)


@router.post("/samples")
async def download_samples(request: Request) -> dict:
    """Fetch the piano samples the browser player uses.

    They are not in the repository (a couple of megabytes of audio), so a fresh
    clone has none and playback falls back to a synthesised tone. Rather than
    making that a setup step people have to know about, the app can fetch them
    itself. Blocking network work, so it runs off the event loop.
    """
    await run_in_threadpool(samples.download)
    return _payload(request)


@router.post("/reset")
async def reset_settings(request: Request) -> dict:
    """Drop the overrides and go back to whatever .env says (after a restart)."""
    request.app.state.settings_store.reset()
    return _payload(request)
