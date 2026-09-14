"""Live USB MIDI on macOS/Windows via rtmidi2, for development away from the Pi.

Optional: `pip install '.[portable]'`, and note rtmidi2 ships wheels only up to
CPython 3.13. The Raspberry Pi deployment never uses this backend.
"""
from __future__ import annotations

import asyncio
import logging

import rtmidi2

from midi_memory.client.midi.source import MidiSource

log = logging.getLogger(__name__)

POLL_SECONDS = 3.0
_SKIP_PORTS = ("midi through", "midi-memory", "iac driver bus 1" )


class PortableSource(MidiSource):
    kind = "portable"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._midi_in: rtmidi2.MidiIn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._open_port: str = ""

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            while not self._closing:
                try:
                    self._poll_ports()
                except Exception:
                    log.exception("rtmidi2 port poll failed")
                await asyncio.sleep(POLL_SECONDS)
        finally:
            self._close_port()

    def _candidates(self) -> list[str]:
        return [
            name for name in rtmidi2.get_in_ports()
            if not any(skip in name.lower() for skip in _SKIP_PORTS) and self.matches(name)
        ]

    def _poll_ports(self) -> None:
        available = self._candidates()
        if self._open_port and self._open_port not in available:
            self._close_port()
        if not self._open_port and available:
            self._open(available[0])

    def _open(self, name: str) -> None:
        midi_in = rtmidi2.MidiIn()
        midi_in.open_port(name)
        midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        midi_in.callback = self._on_message
        self._midi_in = midi_in
        self._open_port = name
        self.set_status(True, name)

    def _on_message(self, message, _timestamp) -> None:
        """Runs on rtmidi's own thread: hand off to the loop and do nothing else."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self.emit_bytes, bytes(message))

    def _close_port(self) -> None:
        if self._midi_in is not None:
            try:
                self._midi_in.callback = None
                self._midi_in.close_port()
            except Exception:
                pass
        self._midi_in = None
        self._open_port = ""
        self.set_status(False, "")

    async def aclose(self) -> None:
        await super().aclose()
        self._close_port()


def list_ports() -> list[str]:
    return list(rtmidi2.get_in_ports())
