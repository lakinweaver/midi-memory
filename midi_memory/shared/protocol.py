"""The wire contract between a capture client and the server.

Both halves import these models, so a change to what a client sends and what the
server stores is a single edit in one file rather than two that drift.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Bumped when the shape below changes incompatibly. The server reports the
# versions it accepts from /api/ingest/hello, so a client that is too old to
# talk to a newly upgraded server says so instead of failing every upload.
PROTOCOL_VERSION = 1

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# The layout of a session directory. The client writes it and the server stores
# the same shape, so the names belong to the contract between them rather than
# to either side.
EVENTS_FILENAME = "events.jsonl"
MIDI_FILENAME = "session.mid"
META_FILENAME = "meta.json"
# The client's own marker rather than anything the server keeps: it is the last
# write a session gets, so its presence is what separates a finished take from
# one that crash recovery still has to finish. The recorder and the spool both
# test for it, which is why it lives here rather than in either of them.
UPLOAD_FILENAME = "upload.json"


class SessionUpload(BaseModel):
    """Everything the server needs to file a recording, computed on the client.

    The client has already parsed the MIDI to render the file, so it sends the
    statistics it derived rather than making the server parse it all again --
    the Pi did that work once already.
    """

    id: str = Field(min_length=8, max_length=40)
    started_at: datetime
    ended_at: datetime
    duration_ms: int = Field(ge=0)
    event_count: int = Field(ge=0)
    note_count: int = Field(ge=0)
    lowest_note: Optional[int] = Field(default=None, ge=0, le=127)
    highest_note: Optional[int] = Field(default=None, ge=0, le=127)
    avg_velocity: Optional[float] = Field(default=None, ge=0, le=127)
    device_name: str = Field(default="", max_length=200)
    fingerprint: list[list[int]] = Field(default_factory=list)

    @classmethod
    def from_record(cls, record: Any) -> "SessionUpload":
        """Build from the client's SessionRecord.

        Typed loosely on purpose: the shared package must not import the client,
        or the server would have to install the capture code to read a payload.
        """
        return cls(
            id=record.id,
            started_at=record.started_at,
            ended_at=record.ended_at,
            duration_ms=record.duration_ms,
            event_count=record.event_count,
            note_count=record.note_count,
            lowest_note=record.lowest_note,
            highest_note=record.highest_note,
            avg_velocity=record.avg_velocity,
            device_name=record.device_name,
            fingerprint=record.fingerprint,
        )


ClientState = Literal["idle", "recording", "offline"]


class Heartbeat(BaseModel):
    """What a client reports every few seconds so the server can show its state."""

    state: ClientState = "idle"
    connected: bool = False
    port_name: str = Field(default="", max_length=200)
    device_name: str = Field(default="", max_length=200)
    note_count: int = Field(default=0, ge=0)
    pending_uploads: int = Field(default=0, ge=0)
    uptime_seconds: int = Field(default=0, ge=0)
    version: str = Field(default="", max_length=40)


class Hello(BaseModel):
    """The server's answer to a client checking it can reach and authenticate."""

    ok: bool = True
    client_id: str
    client_name: str
    protocol_version: int = PROTOCOL_VERSION
    server_version: str = ""


class UploadResult(BaseModel):
    id: str
    status: Literal["stored", "duplicate"]
