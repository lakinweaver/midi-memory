"""The always-on capture service: input source -> recorder -> database -> web UI."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.config import Settings
from app.db import Database
from app.events import EventBus
from app.midi.events import MidiEvent
from app.midi.recorder import MIDI_FILENAME, Recorder, SessionRecord, recover_orphans
from app.midi.smf import extract_notes, fingerprint, read_smf
from app.midi.source import MidiSource, create_source

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0
ACTIVITY_THROTTLE = 0.25  # seconds between "still playing" pings to the browser


class CaptureService:
    def __init__(self, settings: Settings, db: Database, bus: EventBus,
                 source: Optional[MidiSource] = None) -> None:
        self.settings = settings
        self.db = db
        self.bus = bus
        self.source = source or create_source(settings)
        self.source.on_status_change = self._on_status_change

        self.recorder = Recorder(
            settings,
            clock=time.monotonic,
            on_finalized=self._on_finalized,
            on_activity=self._on_activity,
        )
        self._tasks: list[asyncio.Task] = []
        self._last_activity_ping = 0.0
        self.started_at = time.monotonic()

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self.settings.ensure_dirs()

        recovered = recover_orphans(self.settings, self.db.known_ids())
        for record in recovered:
            self.db.insert_session(record)
        if recovered:
            log.info("Recovered %d interrupted session(s) at startup", len(recovered))

        self._tasks = [
            asyncio.create_task(self.source.run(), name="midi-source"),
            asyncio.create_task(self._consume(), name="midi-consume"),
            asyncio.create_task(self._tick(), name="midi-tick"),
            asyncio.create_task(self._backfill_fingerprints(), name="midi-backfill"),
        ]
        log.info("Capture service started (source=%s, idle=%.0fs)",
                 self.source.kind, self.settings.idle_seconds)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.source.aclose()
        # Don't lose whatever was being played when the service went down.
        record = self.recorder.finalize()
        if record is not None:
            log.info("Saved in-progress session %s during shutdown", record.id)

    # -- loops ---------------------------------------------------------------
    async def _consume(self) -> None:
        while True:
            event: MidiEvent = await self.source.queue.get()
            try:
                was_recording = self.recorder.is_recording
                self.recorder.handle(event)
                if not was_recording:
                    self.bus.publish("session_started", id=self.recorder.current_id)
            except Exception:
                log.exception("Failed to record MIDI event")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                self.recorder.tick()
            except Exception:
                log.exception("Recorder tick failed")

    async def _backfill_fingerprints(self) -> None:
        """Give older recordings the pitch strip the library now expects.

        Parsing every MIDI file at once would stall a Pi at startup, so this
        trickles through them, yielding between each.
        """
        await asyncio.sleep(2)   # let the app finish coming up first
        pending = self.db.ids_missing_fingerprint()
        if not pending:
            return
        log.info("Backfilling pitch strips for %d recording(s)", len(pending))
        for session_id in pending:
            path = self.settings.sessions_dir / session_id / MIDI_FILENAME
            points = []
            if path.exists():
                try:
                    points = fingerprint(extract_notes(read_smf(path)))
                except Exception:
                    log.exception("Could not build a pitch strip for %s", session_id)
            self.db.set_fingerprint(session_id, points)
            await asyncio.sleep(0.05)
        log.info("Pitch strip backfill complete")

    # -- callbacks -----------------------------------------------------------
    def _on_finalized(self, record: SessionRecord) -> None:
        try:
            self.db.insert_session(record)
        except Exception:
            log.exception("Failed to save session %s", record.id)
            return
        session = self.db.get_session(record.id)
        log.info("Session %s saved (%d notes, %.1fs)",
                 record.id, record.note_count, record.duration_ms / 1000)
        self.bus.publish("session_saved", session=session)

    def _on_activity(self, event: MidiEvent) -> None:
        if not event.is_note_on:
            return
        now = time.monotonic()
        if now - self._last_activity_ping < ACTIVITY_THROTTLE:
            return
        self._last_activity_ping = now
        self.bus.publish(
            "activity",
            note=event.data1,
            velocity=event.data2,
            session_id=self.recorder.current_id,
            note_count=self.recorder.current_note_count,
        )

    def _on_status_change(self, connected: bool, port_name: str) -> None:
        self.bus.publish("device_status", connected=connected, port_name=port_name)

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        return {
            "source": self.source.kind,
            "connected": self.source.connected,
            "port_name": self.source.port_name,
            "recording": self.recorder.is_recording,
            "current_session_id": self.recorder.current_id,
            "current_note_count": self.recorder.current_note_count,
            "seconds_since_activity": round(self.recorder.seconds_since_activity(), 1),
            "idle_seconds": self.settings.idle_seconds,
            "uptime_seconds": round(time.monotonic() - self.started_at),
            "queue_depth": self.source.queue.qsize(),
        }
