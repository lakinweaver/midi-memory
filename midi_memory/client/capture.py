"""The always-on capture service: input source -> marker -> recorder -> spool -> uploader.

This is the whole job of a client. It owns no library and no database: a finished
take is handed to the spool, and the uploader carries it to the server whenever
the server happens to be reachable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from midi_memory.client.config import Settings
from midi_memory.client.midi.marker import Decision, MarkerListener
from midi_memory.client.midi.recorder import Recorder, SessionRecord, recover_orphans
from midi_memory.client.midi.source import MidiSource, create_source
from midi_memory.client.spool import Spool
from midi_memory.shared.midi.events import MidiEvent

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0
PRUNE_SECONDS = 3600.0


class CaptureService:
    def __init__(self, settings: Settings, spool: Spool,
                 source: Optional[MidiSource] = None,
                 on_session: Optional[callable] = None) -> None:
        self.settings = settings
        self.spool = spool
        self.on_session = on_session   # the uploader's nudge to drain now
        self.source = source or create_source(settings)
        self.source.on_status_change = self._on_status_change

        self.recorder = Recorder(
            settings,
            clock=time.monotonic,
            on_finalized=self._on_finalized,
        )
        # The marker sits in front of the recorder rather than inside it: ending a
        # take on a gesture is a decision about the stream, not about segmenting
        # silence, which is all the recorder knows how to do.
        self.marker = MarkerListener(settings)
        self.marker_ended_at: Optional[float] = None
        self._tasks: list[asyncio.Task] = []
        self.started_at = time.monotonic()

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self.settings.ensure_dirs()

        # Start-time order, so the spool's finish-order queue is also play order.
        recovered = sorted(recover_orphans(self.settings), key=lambda r: r.started_at)
        for record in recovered:
            self.spool.add(record)
        if recovered:
            log.info("Recovered %d interrupted session(s) at startup", len(recovered))

        self._tasks = [
            asyncio.create_task(self.source.run(), name="midi-source"),
            asyncio.create_task(self._consume(), name="midi-consume"),
            asyncio.create_task(self._tick(), name="midi-tick"),
            asyncio.create_task(self._prune(), name="spool-prune"),
        ]
        log.info("Capture started (source=%s, idle=%.0fs)",
                 self.source.kind, self.settings.idle_seconds)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.source.aclose()
        # Don't lose whatever was being played when the service went down --
        # including a press the marker was still deciding about.
        self._dispatch(Decision(self.marker.drain()))
        record = self.recorder.finalize()
        if record is not None:
            log.info("Saved in-progress session %s during shutdown", record.id)

    # -- loops ---------------------------------------------------------------
    async def _consume(self) -> None:
        while True:
            event: MidiEvent = await self.source.queue.get()
            try:
                self._dispatch(self.marker.feed(event))
            except Exception:
                log.exception("Failed to record MIDI event")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                # Before the idle check, so a press that turned out to be a note
                # counts as activity rather than arriving just after the cut.
                self._dispatch(Decision(self.marker.expire(time.monotonic())))
                self.recorder.tick()
            except Exception:
                log.exception("Recorder tick failed")

    async def _prune(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_SECONDS)
            try:
                self.spool.prune()
            except Exception:
                log.exception("Pruning uploaded sessions failed")

    # -- callbacks -----------------------------------------------------------
    def _dispatch(self, decision: Decision) -> None:
        for event in decision.record:
            self.recorder.handle(event)
        if decision.end_session:
            self._end_on_marker()

    def _end_on_marker(self) -> None:
        # Stamped even when nothing was open, because the page shows this to
        # confirm the gesture registered -- and "nothing happened" is the one
        # case where the player most needs telling that it did.
        self.marker_ended_at = time.monotonic()
        if not self.recorder.is_recording:
            log.info("Marker key pressed twice while idle; nothing to end")
            return
        log.info("Marker key ended session %s", self.recorder.current_id)
        self.recorder.finalize()

    def _on_finalized(self, record: SessionRecord) -> None:
        try:
            self.spool.add(record)
        except Exception:
            log.exception("Failed to spool session %s", record.id)
            return
        log.info("Session %s captured (%d notes, %.1fs)",
                 record.id, record.note_count, record.duration_ms / 1000)
        if self.on_session is not None:
            self.on_session(record)

    def _on_status_change(self, connected: bool, port_name: str) -> None:
        # Stamp the port onto the recorder so the take carries the name of the
        # instrument it came from. Sessions already open keep the name they
        # started with; only the next one picks up a newly plugged keyboard.
        self.recorder.device_name = port_name if connected else ""
        log.info("Input %s%s", "connected: " if connected else "disconnected", port_name)

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        return {
            "source": self.source.kind,
            "connected": self.source.connected,
            "port_name": self.source.port_name,
            "recording": self.recorder.is_recording,
            "state": "recording" if self.recorder.is_recording else "idle",
            "current_session_id": self.recorder.current_id,
            "current_note_count": self.recorder.current_note_count,
            "seconds_since_activity": round(self.recorder.seconds_since_activity(), 1),
            "idle_seconds": self.settings.idle_seconds,
            "marker_note": self.settings.marker_note if self.settings.marker_enabled else None,
            "seconds_since_marker": (
                None if self.marker_ended_at is None
                else round(time.monotonic() - self.marker_ended_at, 1)
            ),
            "uptime_seconds": round(time.monotonic() - self.started_at),
            "queue_depth": self.source.queue.qsize(),
        }
