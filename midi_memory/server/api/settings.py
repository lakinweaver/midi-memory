"""Server settings.

Capture settings are not here: they belong to whichever client is doing the
recording, and are edited on that client's own page. What is left is the piano
sample set the browser player uses, and a read-only view of how this server was
started.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from midi_memory.server import samples

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _payload(request: Request) -> dict:
    s = request.app.state.settings
    return {
        "read_only": {
            "host": s.host,
            "port": s.port,
            "data_dir": str(s.data_dir),
            "auth_enabled": s.auth_enabled,
            "timezone": s.timezone_label,
        },
        "samples": samples.status(),
        "clients": request.app.state.clients.listing(),
    }


@router.get("")
async def get_settings(request: Request) -> dict:
    return _payload(request)


@router.post("/samples")
async def download_samples(request: Request) -> dict:
    """Fetch the piano samples the browser player uses.

    They are not in the repository (a couple of megabytes of audio), so an image
    built without them falls back to a synthesised tone. Rather than making that
    a setup step people have to know about, the app can fetch them itself.
    Blocking network work, so it runs off the event loop.
    """
    await run_in_threadpool(samples.download)
    return _payload(request)
