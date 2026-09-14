"""Where recordings arrive from capture clients.

This is the only part of the server a client ever talks to, and the only part
authenticated with a bearer secret rather than the browser's login cookie.
"""
from __future__ import annotations

import logging
import shutil
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from midi_memory.shared.protocol import (
    EVENTS_FILENAME,
    MAX_UPLOAD_BYTES,
    MIDI_FILENAME,
    PROTOCOL_VERSION,
    Heartbeat,
    Hello,
    SessionUpload,
    UploadResult,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


async def current_client(request: Request) -> dict:
    """Resolve the bearer secret to a registered, unrevoked client."""
    header = request.headers.get("authorization", "")
    scheme, _, secret = header.partition(" ")
    if scheme.lower() != "bearer" or not secret:
        raise HTTPException(status_code=401, detail="Missing client secret",
                            headers={"WWW-Authenticate": "Bearer"})
    client = request.app.state.clients.authenticate(secret.strip())
    if client is None:
        raise HTTPException(status_code=403, detail="Unknown or revoked client")
    return client


Client = Annotated[dict, Depends(current_client)]


@router.post("/hello")
async def hello(request: Request, client: Client) -> Hello:
    """Handshake: proves the address is right and the secret is accepted."""
    return Hello(
        client_id=client["id"],
        client_name=client["name"],
        protocol_version=PROTOCOL_VERSION,
        server_version=request.app.version,
    )


@router.post("/heartbeat")
async def heartbeat(request: Request, client: Client, beat: Heartbeat) -> dict:
    request.app.state.clients.heartbeat(client["id"], beat)
    return {"ok": True}


@router.post("/sessions")
async def upload_session(
    request: Request,
    client: Client,
    metadata: Annotated[str, Form()],
    midi: Annotated[UploadFile, File()],
    events: Annotated[Optional[UploadFile], File()] = None,
):
    try:
        payload = SessionUpload.model_validate_json(metadata)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    db = request.app.state.db
    owner = db.session_owner(payload.id)
    if owner == client["id"]:
        # A retry after an acknowledgement we never managed to send back. Saying
        # so plainly lets the client retire it instead of trying forever.
        return UploadResult(id=payload.id, status="duplicate")
    if owner is not None:
        raise HTTPException(
            status_code=409,
            detail="That session id already belongs to another client",
        )

    midi_bytes = await _read_capped(midi)
    if not midi_bytes:
        raise HTTPException(status_code=422, detail="The MIDI file was empty")

    settings = request.app.state.settings
    directory = settings.sessions_dir / payload.id
    directory.mkdir(parents=True, exist_ok=True)
    try:
        (directory / MIDI_FILENAME).write_bytes(midi_bytes)
        if events is not None:
            (directory / EVENTS_FILENAME).write_bytes(await _read_capped(events))
        db.insert_session(payload, client_id=client["id"])
    except Exception:
        # Never leave a directory the library has no row for: the next restart
        # would find it and have no idea what it was.
        shutil.rmtree(directory, ignore_errors=True)
        log.exception("Failed to store session %s from %s", payload.id, client["name"])
        raise HTTPException(status_code=500, detail="Could not store the recording")

    session = db.get_session(payload.id)
    log.info("Stored session %s from %s (%d notes)",
             payload.id, client["name"], payload.note_count)
    request.app.state.bus.publish("session_saved", session=session)
    return UploadResult(id=payload.id, status="stored")


async def _read_capped(upload: UploadFile) -> bytes:
    """Read an upload, refusing anything past the documented ceiling.

    Read in chunks rather than all at once: the point of a limit is not to load
    the thing before deciding it is too big.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="That recording is too large")
        chunks.append(chunk)
    return b"".join(chunks)
