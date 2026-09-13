"""Transport control for playing a session out to the attached instrument.

There is one instrument, so this is global state rather than per-browser: every
connected tab drives and observes the same transport.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.midi.device_player import PlaybackUnavailable

router = APIRouter(prefix="/api/playback", tags=["playback"])


class PlayRequest(BaseModel):
    session_id: str
    position: float = Field(default=0.0, ge=0)
    speed: float = Field(default=1.0, gt=0, le=4)
    loop: bool = False


class SeekRequest(BaseModel):
    position: float = Field(ge=0)


class ConfigRequest(BaseModel):
    speed: Optional[float] = Field(default=None, gt=0, le=4)
    loop: Optional[bool] = None


@router.get("")
async def get_playback(request: Request) -> dict:
    return request.app.state.service.player.status()


@router.post("/play")
async def start_playback(request: Request, payload: PlayRequest) -> dict:
    service = request.app.state.service
    if request.app.state.db.get_session(payload.session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return await service.player.play(
            payload.session_id, position=payload.position,
            speed=payload.speed, loop=payload.loop,
        )
    except PlaybackUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
async def stop_playback(request: Request) -> dict:
    return await request.app.state.service.player.stop()


@router.post("/seek")
async def seek_playback(request: Request, payload: SeekRequest) -> dict:
    return await request.app.state.service.player.seek(payload.position)


@router.post("/config")
async def configure_playback(request: Request, payload: ConfigRequest) -> dict:
    return await request.app.state.service.player.configure(
        speed=payload.speed, loop=payload.loop
    )
