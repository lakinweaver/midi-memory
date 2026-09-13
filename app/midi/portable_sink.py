"""MIDI output on macOS/Windows via rtmidi2, for development away from the Pi."""
from __future__ import annotations

import asyncio
import logging

import rtmidi2

from app.midi.events import MidiEvent
from app.midi.sink import MidiSink

log = logging.getLogger(__name__)

POLL_SECONDS = 3.0
_SKIP_PORTS = ("midi through", "midi-memory")


class PortableSink(MidiSink):
    kind = "portable"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._midi_out: rtmidi2.MidiOut | None = None
        self._open_port: str = ""
        self._task: asyncio.Task | None = None

    async def open(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._maintain(), name="midi-sink")

    async def _maintain(self) -> None:
        while not self._closing:
            try:
                available = [
                    n for n in rtmidi2.get_out_ports()
                    if not any(s in n.lower() for s in _SKIP_PORTS) and self.matches(n)
                ]
                if self._open_port and self._open_port not in available:
                    self._close_port()
                if not self._open_port and available:
                    midi_out = rtmidi2.MidiOut()
                    midi_out.open_port(available[0])
                    self._midi_out = midi_out
                    self._open_port = available[0]
                    self.set_status(True, available[0])
                    await self.panic(force=True)
            except Exception:
                log.exception("rtmidi2 output poll failed")
            await asyncio.sleep(POLL_SECONDS)

    async def _deliver(self, event: MidiEvent) -> None:
        if self._midi_out is None:
            return
        try:
            if event.kind in (0xC0, 0xD0):
                self._midi_out.send_raw(event.status, event.data1 & 0x7F)
            else:
                self._midi_out.send_raw(event.status, event.data1 & 0x7F, event.data2 & 0x7F)
        except Exception:
            log.exception("Failed to send MIDI output")

    def _close_port(self) -> None:
        if self._midi_out is not None:
            try:
                self._midi_out.close_port()
            except Exception:
                pass
        self._midi_out = None
        self._open_port = ""
        self.set_status(False, "")

    async def aclose(self) -> None:
        await super().aclose()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._close_port()
