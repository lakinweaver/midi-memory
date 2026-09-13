"""Device/recorder status and the live SSE stream."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api", tags=["status"])

HEARTBEAT_SECONDS = 20


@router.get("/status")
async def get_status(request: Request) -> dict:
    service = request.app.state.service
    return {**service.status(), "stats": request.app.state.db.stats()}


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: device status, recording activity, newly saved sessions."""
    bus = request.app.state.bus
    service = request.app.state.service

    async def generate():
        with bus.subscribe() as queue:
            # Send current state immediately so a fresh tab is never blank.
            yield _sse({"type": "status", **service.status()})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Comment frame: keeps proxies and phones from dropping the socket.
                    yield ": keepalive\n\n"
                    continue
                yield _sse(message)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
