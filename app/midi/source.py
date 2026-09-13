"""MIDI input sources.

Every source converts its native dialect into `MidiEvent` and pushes onto an
asyncio queue, so the recorder never knows or cares which backend is in use.

All sources timestamp with `time.monotonic()` at the moment of receipt. On a Pi the
USB-to-userspace jitter is well under a millisecond, which is far finer than anything
that matters for a musical notepad, and using one clock everywhere keeps replay,
recovery and tests consistent.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import platform
import time
from typing import Callable, Optional

from app.config import Settings
from app.midi.events import MidiEvent, parse_bytes

log = logging.getLogger(__name__)

QUEUE_MAXSIZE = 20_000


class MidiSource(abc.ABC):
    """Base class: owns the queue, the connection status, and the status callback."""

    kind = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.queue: asyncio.Queue[MidiEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.port_name: str = ""
        self.connected: bool = False
        self.on_status_change: Optional[Callable[[bool, str], None]] = None
        self._closing = False

    # -- lifecycle -----------------------------------------------------------
    @abc.abstractmethod
    async def run(self) -> None:
        """Long-running task that feeds `self.queue` until cancelled."""

    async def aclose(self) -> None:
        self._closing = True

    # -- helpers for subclasses ---------------------------------------------
    def emit_bytes(self, data) -> None:
        event = parse_bytes(time.monotonic(), data)
        if event is None:
            return  # active sensing, clock, sysex: never musical activity
        self.emit(event)

    def emit(self, event: MidiEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("MIDI queue full; dropping event")

    def set_status(self, connected: bool, port_name: str = "") -> None:
        if connected == self.connected and port_name == self.port_name:
            return
        self.connected = connected
        self.port_name = port_name
        log.info("MIDI device %s%s", "connected: " + port_name if connected else "disconnected", "")
        if self.on_status_change is not None:
            self.on_status_change(connected, port_name)

    def matches(self, name: str) -> bool:
        wanted = self.settings.device_match.strip().lower()
        return wanted in name.lower() if wanted else True


class NullSource(MidiSource):
    """No MIDI input at all. Lets the web UI run on a machine with no hardware."""

    kind = "none"

    async def run(self) -> None:
        self.set_status(False, "")
        while not self._closing:
            await asyncio.sleep(3600)


def create_source(settings: Settings) -> MidiSource:
    """Pick the best available input backend for this machine."""
    choice = (settings.midi_source or "auto").lower()

    if choice == "mock":
        from app.midi.mock_source import MockSource
        return MockSource(settings)
    if choice == "none":
        return NullSource(settings)
    if choice == "alsa":
        from app.midi.alsa_source import AlsaSource
        return AlsaSource(settings)
    if choice == "portable":
        from app.midi.portable_source import PortableSource
        return PortableSource(settings)

    # auto: prefer the native ALSA backend on Linux, then rtmidi2, then nothing.
    if platform.system() == "Linux":
        try:
            from app.midi.alsa_source import AlsaSource
            return AlsaSource(settings)
        except ImportError:
            log.warning("alsa-midi not installed; falling back. Install with: pip install '.[alsa]'")
    try:
        from app.midi.portable_source import PortableSource
        return PortableSource(settings)
    except ImportError:
        pass

    log.warning(
        "No MIDI backend available on this machine - running without live input. "
        "Set MIDI_MEMORY_MIDI_SOURCE=mock to generate test sessions."
    )
    return NullSource(settings)
