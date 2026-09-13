"""Always-on capture: turns an endless stream of MIDI events into discrete sessions.

The recorder never has a 'record' button. It opens a session on the first musical
event, appends every event to disk as it arrives, and closes the session once the
keyboard has been quiet for `idle_seconds`.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, TextIO

from app.config import Settings
from app.midi.events import MidiEvent
from app.midi.smf import compute_stats, write_smf

log = logging.getLogger(__name__)

EVENTS_FILENAME = "events.jsonl"
MIDI_FILENAME = "session.mid"
META_FILENAME = "meta.json"


@dataclass(slots=True)
class SessionRecord:
    """A finalised session, ready to be written to the database."""

    id: str
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    event_count: int
    note_count: int
    lowest_note: Optional[int]
    highest_note: Optional[int]
    avg_velocity: Optional[float]
    device_name: str
    directory: Path

    @property
    def midi_path(self) -> Path:
        return self.directory / MIDI_FILENAME


@dataclass(slots=True)
class _Pending:
    """A session currently being recorded."""

    id: str
    directory: Path
    started_at: datetime
    origin: float  # monotonic timestamp of the first event
    handle: TextIO
    events: list[MidiEvent] = field(default_factory=list)
    device_name: str = ""


class Recorder:
    """Segments a MIDI stream into sessions. Pure logic + file IO; no database."""

    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], float],
        on_finalized: Optional[Callable[[SessionRecord], None]] = None,
        on_activity: Optional[Callable[[MidiEvent], None]] = None,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.wall_clock = wall_clock
        self.on_finalized = on_finalized
        self.on_activity = on_activity

        self._pending: Optional[_Pending] = None
        self._last_activity: float = 0.0
        self._held_notes: set[tuple[int, int]] = set()
        self._sustain_on = False
        self.device_name = ""

    # -- introspection -------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._pending is not None

    @property
    def current_id(self) -> Optional[str]:
        return self._pending.id if self._pending else None

    @property
    def current_note_count(self) -> int:
        if not self._pending:
            return 0
        return sum(1 for e in self._pending.events if e.is_note_on)

    def seconds_since_activity(self) -> float:
        if not self._pending:
            return 0.0
        return max(0.0, self.clock() - self._last_activity)

    # -- ingestion -----------------------------------------------------------
    def handle(self, event: MidiEvent) -> None:
        """Feed one recordable event into the recorder."""
        if self._pending is None:
            self._open(event)

        pending = self._pending
        assert pending is not None

        relative = event.shifted(pending.origin)
        pending.events.append(relative)
        pending.handle.write(json.dumps(relative.to_row(), separators=(",", ":")) + "\n")
        # Flush every event: a power cut on a Pi should cost at most one note.
        pending.handle.flush()

        self._track_state(event)
        self._last_activity = max(self._last_activity, event.t)

        if self.on_activity is not None:
            self.on_activity(event)

    def _track_state(self, event: MidiEvent) -> None:
        key = (event.channel, event.data1)
        if event.is_note_on:
            self._held_notes.add(key)
        elif event.is_note_off:
            self._held_notes.discard(key)
        elif event.is_sustain_down:
            self._sustain_on = True
        elif event.is_sustain_up:
            self._sustain_on = False

    # -- segmentation --------------------------------------------------------
    def tick(self) -> Optional[SessionRecord]:
        """Called roughly once a second; closes the session when it has gone idle."""
        if self._pending is None:
            return None

        idle = self.clock() - self._last_activity
        if idle < self.settings.idle_seconds:
            return None

        # Don't guillotine a long final chord: wait for the keys and pedal to come up.
        # The hard ceiling protects against a note left stuck by an unplugged cable.
        if self._held_notes or self._sustain_on:
            if idle < self.settings.idle_seconds * 4:
                return None
            log.warning(
                "Force-closing session %s after %.0fs with %d note(s) still held",
                self._pending.id, idle, len(self._held_notes),
            )

        return self.finalize()

    def _open(self, event: MidiEvent) -> None:
        session_id = uuid.uuid4().hex[:16]
        directory = self.settings.sessions_dir / session_id
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / EVENTS_FILENAME).open("w", encoding="utf-8")

        self._pending = _Pending(
            id=session_id,
            directory=directory,
            started_at=self.wall_clock(),
            origin=event.t,
            handle=handle,
            device_name=self.device_name,
        )
        self._last_activity = event.t
        self._held_notes.clear()
        self._sustain_on = False

        # A sidecar so crash recovery knows when this session actually began.
        (directory / META_FILENAME).write_text(
            json.dumps({
                "id": session_id,
                "started_at": self._pending.started_at.isoformat(),
                "device_name": self._pending.device_name,
            }),
            encoding="utf-8",
        )
        log.info("Session %s opened", session_id)

    def finalize(self) -> Optional[SessionRecord]:
        """Close the open session. Returns None if it was too trivial to keep."""
        pending = self._pending
        if pending is None:
            return None
        self._pending = None
        self._held_notes.clear()
        self._sustain_on = False

        try:
            pending.handle.close()
        except OSError:
            pass

        record = finalize_directory(
            pending.directory,
            self.settings,
            events=pending.events,
            started_at=pending.started_at,
            device_name=pending.device_name,
        )
        if record is not None and self.on_finalized is not None:
            self.on_finalized(record)
        return record


def _load_events(directory: Path) -> list[MidiEvent]:
    path = directory / EVENTS_FILENAME
    if not path.exists():
        return []
    events: list[MidiEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(MidiEvent.from_row(json.loads(line)))
            except (ValueError, KeyError):
                # A torn final line from an abrupt power cut: ignore it and keep the rest.
                continue
    return events


def _discard(directory: Path) -> None:
    for child in directory.glob("*"):
        try:
            child.unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


def finalize_directory(
    directory: Path,
    settings: Settings,
    events: Optional[list[MidiEvent]] = None,
    started_at: Optional[datetime] = None,
    device_name: str = "",
) -> Optional[SessionRecord]:
    """Render a session directory to a MIDI file and summarise it.

    Shared by the live recorder and by startup crash recovery, so a session
    interrupted by a power cut is finalised exactly the same way.
    """
    if events is None:
        events = _load_events(directory)

    meta_path = directory / META_FILENAME
    if (started_at is None or not device_name) and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            started_at = started_at or datetime.fromisoformat(meta["started_at"])
            device_name = device_name or meta.get("device_name", "")
        except (ValueError, KeyError, OSError):
            pass
    if started_at is None:
        started_at = datetime.now(timezone.utc)

    stats = compute_stats(events)

    too_short = stats["duration_ms"] < settings.min_seconds * 1000
    too_few = stats["note_count"] < settings.min_notes
    if too_few or too_short:
        log.info(
            "Discarding trivial session %s (%d notes, %dms)",
            directory.name, stats["note_count"], stats["duration_ms"],
        )
        _discard(directory)
        return None

    write_smf(events, directory / MIDI_FILENAME, name=directory.name)

    return SessionRecord(
        id=directory.name,
        started_at=started_at,
        ended_at=started_at + timedelta(milliseconds=stats["duration_ms"]),
        duration_ms=stats["duration_ms"],
        event_count=stats["event_count"],
        note_count=stats["note_count"],
        lowest_note=stats["lowest_note"],
        highest_note=stats["highest_note"],
        avg_velocity=stats["avg_velocity"],
        device_name=device_name,
        directory=directory,
    )


def recover_orphans(settings: Settings, known_ids: set[str]) -> list[SessionRecord]:
    """Finalise session directories that were interrupted before being saved."""
    recovered: list[SessionRecord] = []
    if not settings.sessions_dir.exists():
        return recovered
    for directory in sorted(settings.sessions_dir.iterdir()):
        if not directory.is_dir() or directory.name in known_ids:
            continue
        if not (directory / EVENTS_FILENAME).exists():
            continue
        log.warning("Recovering interrupted session %s", directory.name)
        record = finalize_directory(directory, settings)
        if record is not None:
            recovered.append(record)
    return recovered
