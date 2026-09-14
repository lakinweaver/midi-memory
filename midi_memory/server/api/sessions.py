"""Session browsing: search, playback data, editing, download, delete."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from midi_memory.shared.protocol import MIDI_FILENAME
from midi_memory.shared.midi.smf import extract_notes, read_smf

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=5000)
    favorite: Optional[bool] = None


class TagsUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


@router.get("")
async def search_sessions(
    request: Request,
    q: str = "",
    tag: Annotated[list[str], Query()] = [],
    date_from: str = "",
    date_to: str = "",
    min_duration_ms: Optional[int] = None,
    max_duration_ms: Optional[int] = None,
    favorite: bool = False,
    client_id: str = "",
    sort: str = "date",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return request.app.state.db.search(
        q=q, tags=tag, date_from=date_from, date_to=date_to,
        min_duration_ms=min_duration_ms, max_duration_ms=max_duration_ms,
        favorite_only=favorite, client_id=client_id,
        sort=sort, order=order, limit=limit, offset=offset,
    )


@router.get("/{session_id}")
async def get_session(request: Request, session_id: str) -> dict:
    session = _require(request, session_id)
    return session


@router.get("/{session_id}/notes")
async def get_session_notes(request: Request, session_id: str) -> dict:
    """Note list for the browser player and piano roll.

    The pedal is resolved here rather than in JavaScript, so the client only has to
    schedule plain note-on/note-off pairs.
    """
    session = _require(request, session_id)
    directory = request.app.state.settings.sessions_dir / session_id
    midi_path = directory / MIDI_FILENAME
    if not midi_path.exists():
        raise HTTPException(status_code=404, detail="Recording file is missing")

    notes = extract_notes(read_smf(midi_path))
    return {
        "id": session_id,
        "name": session["name"],
        "duration_ms": session["duration_ms"],
        "lowest_note": session["lowest_note"],
        "highest_note": session["highest_note"],
        "notes": [n.to_row() for n in notes],
    }


@router.get("/{session_id}/download")
async def download_session(request: Request, session_id: str) -> FileResponse:
    session = _require(request, session_id)
    path = request.app.state.settings.sessions_dir / session_id / MIDI_FILENAME
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording file is missing")
    return FileResponse(
        path,
        media_type="audio/midi",
        filename=f"{_safe_filename(session['name'])}.mid",
    )


@router.patch("/{session_id}")
async def update_session(request: Request, session_id: str,
                         payload: SessionUpdate) -> dict:
    _require(request, session_id)
    fields = payload.model_dump(exclude_none=True)
    if "favorite" in fields:
        fields["favorite"] = int(fields["favorite"])
    if "name" in fields:
        fields["name"] = fields["name"].strip() or "Untitled"
    if fields:
        request.app.state.db.update_session(session_id, **fields)
    return _require(request, session_id)


@router.put("/{session_id}/tags")
async def set_tags(request: Request, session_id: str, payload: TagsUpdate) -> dict:
    _require(request, session_id)
    request.app.state.db.set_session_tags(session_id, payload.tags)
    return _require(request, session_id)


@router.post("/{session_id}/tags")
async def add_tag(request: Request, session_id: str,
                  tag: Annotated[str, Body(embed=True)]) -> dict:
    _require(request, session_id)
    request.app.state.db.add_session_tag(session_id, tag)
    return _require(request, session_id)


@router.delete("/{session_id}/tags/{tag}")
async def remove_tag(request: Request, session_id: str, tag: str) -> dict:
    _require(request, session_id)
    request.app.state.db.remove_session_tag(session_id, tag)
    return _require(request, session_id)


@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict:
    _require(request, session_id)
    settings = request.app.state.settings
    request.app.state.db.delete_session(session_id, settings.sessions_dir)
    return {"deleted": session_id}


# -- helpers -----------------------------------------------------------------
def _require(request: Request, session_id: str) -> dict:
    session = request.app.state.db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _safe_filename(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in " -_") else "-" for c in name).strip()
    return (keep or "session")[:80]
