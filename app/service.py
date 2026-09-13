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
from app.midi.device_player import DevicePlayer
from app.midi.recorder import MIDI_FILENAME, Recorder, SessionRecord, recover_orphans
from app.midi.sink import MidiSink, create_sink
from app.midi.smf import read_smf
from app.midi.source import MidiSource, create_source

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0
ACTIVITY_THROTTLE = 0.25  # seconds between "still playing" pings to the browser
ECHO_GUARD_SECONDS = 0.75  # keep ignoring input just after playback stops


class CaptureService:
    def __init__(self, settings: Settings, db: Database, bus: EventBus,
                 source: Optional[MidiSource] = None,
                 sink: Optional[MidiSink] = None) -> None:
        self.settings = settings
        self.db = db
        self.bus = bus
        self.source = source or create_source(settings)
        self.source.on_status_change = self._on_status_change

        self.sink = sink if sink is not None else create_sink(settings)
        self.sink.on_status_change = self._on_output_status_change
        self.player = DevicePlayer(
            self.sink,
            load_events=self._load_events,
            on_change=self._on_playback_change,
        )
        self._playback_ended_at = 0.0

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

        await self.sink.open()

        self._tasks = [
            asyncio.create_task(self.source.run(), name="midi-source"),
            asyncio.create_task(self._consume(), name="midi-consume"),
            asyncio.create_task(self._tick(), name="midi-tick"),
        ]
        log.info("Capture service started (source=%s, idle=%.0fs)",
                 self.source.kind, self.settings.idle_seconds)

    async def stop(self) -> None:
        try:
            await self.player.stop()
        except Exception:
            log.exception("Failed to stop device playback")
        await self.sink.aclose()

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
    def _suppress_capture(self) -> bool:
        """True while playback owns the instrument.

        Many digital pianos echo MIDI in straight back out of MIDI out. Without
        this, playing a session to the piano would be recorded as a brand-new
        session, which would then play back and record itself again. The guard
        window catches echoes still in flight when playback stops.
        """
        if self.settings.capture_during_playback:
            return False
        if self.player.playing:
            return True
        return (time.monotonic() - self._playback_ended_at) < ECHO_GUARD_SECONDS

    async def _consume(self) -> None:
        while True:
            event: MidiEvent = await self.source.queue.get()
            if self._suppress_capture():
                continue
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

    def _on_output_status_change(self, connected: bool, port_name: str) -> None:
        self.bus.publish("output_status", connected=connected, port_name=port_name)

    def _on_playback_change(self, status: dict) -> None:
        if not status.get("playing"):
            self._playback_ended_at = time.monotonic()
        self.bus.publish("playback", playback=status)

    def _load_events(self, session_id: str) -> list[MidiEvent]:
        path = self.settings.sessions_dir / session_id / MIDI_FILENAME
        if not path.exists():
            return []
        try:
            return read_smf(path)
        except Exception:
            log.exception("Could not read recording %s for playback", session_id)
            return []

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
            "capture_suppressed": self._suppress_capture(),
            "output": self.sink.status(),
            "playback": self.player.status(),
        }
