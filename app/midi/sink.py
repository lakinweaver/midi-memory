"""MIDI output: sending recorded performances back out to the attached instrument.

The Pi holds the USB connection to the piano, so "play it on the piano" has to
happen server-side. Each backend converts `MidiEvent` into its own dialect; the
player above never knows which one is in use.
"""
from __future__ import annotations

import abc
import logging
import platform
from typing import Callable, Optional

from app.config import Settings
from app.midi.events import CC_ALL_NOTES_OFF, CC_SUSTAIN, MidiEvent

log = logging.getLogger(__name__)


class MidiSink(abc.ABC):
    """Base class: connection state plus held-note bookkeeping for clean stops."""

    kind = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.port_name: str = ""
        self.connected: bool = False
        self.on_status_change: Optional[Callable[[bool, str], None]] = None
        self._held: set[tuple[int, int]] = set()   # (channel, note)
        self._sustained: set[int] = set()          # channels with the pedal down
        self._closing = False

    # -- lifecycle -----------------------------------------------------------
    async def open(self) -> None:
        """Start looking for an output device. Safe to call repeatedly."""

    async def aclose(self) -> None:
        self._closing = True

    @property
    def available(self) -> bool:
        """Whether this backend could ever produce output on this machine."""
        return True

    # -- sending -------------------------------------------------------------
    async def send(self, event: MidiEvent) -> None:
        if not self.connected:
            return
        self._track(event)
        await self._deliver(event)

    @abc.abstractmethod
    async def _deliver(self, event: MidiEvent) -> None:
        """Backend-specific write of a single channel-voice message."""

    def _track(self, event: MidiEvent) -> None:
        key = (event.channel, event.data1)
        if event.is_note_on:
            self._held.add(key)
        elif event.is_note_off:
            self._held.discard(key)
        elif event.is_sustain_down:
            self._sustained.add(event.channel)
        elif event.is_sustain_up:
            self._sustained.discard(event.channel)

    async def panic(self, force: bool = False) -> None:
        """Silence the instrument.

        Stopping mid-phrase otherwise leaves the piano ringing until someone
        power-cycles it. Explicit note-offs come first because some instruments
        ignore All Notes Off, then the pedal is lifted, then the blanket reset.

        Does nothing when we are not holding anything, so starting playback does
        not spray a pointless reset at the instrument first. `force` overrides
        that for the one case where our bookkeeping cannot be trusted: a fresh
        connection, where a previous crash may have left notes ringing.
        """
        if not self.connected:
            self._held.clear()
            self._sustained.clear()
            return
        if not force and not self._held and not self._sustained:
            return

        for channel, note in sorted(self._held):
            await self._deliver(MidiEvent(0.0, 0x80 | channel, note, 0))
        for channel in sorted(self._sustained):
            await self._deliver(MidiEvent(0.0, 0xB0 | channel, CC_SUSTAIN, 0))
        for channel in sorted({c for c, _ in self._held} | self._sustained | {0}):
            await self._deliver(MidiEvent(0.0, 0xB0 | channel, CC_ALL_NOTES_OFF, 0))

        self._held.clear()
        self._sustained.clear()

    # -- helpers -------------------------------------------------------------
    def set_status(self, connected: bool, port_name: str = "") -> None:
        if connected == self.connected and port_name == self.port_name:
            return
        self.connected = connected
        self.port_name = port_name
        log.info("MIDI output %s", f"connected: {port_name}" if connected else "disconnected")
        if self.on_status_change is not None:
            self.on_status_change(connected, port_name)

    def matches(self, name: str) -> bool:
        wanted = self.settings.device_match.strip().lower()
        return wanted in name.lower() if wanted else True

    def status(self) -> dict:
        return {
            "kind": self.kind,
            "available": self.available,
            "connected": self.connected,
            "port_name": self.port_name,
        }


class NullSink(MidiSink):
    """No output hardware. The UI offers browser playback only."""

    kind = "none"

    @property
    def available(self) -> bool:
        return False

    async def _deliver(self, event: MidiEvent) -> None:
        return


class MockSink(MidiSink):
    """Records what would have been sent, so playback can be tested without a piano."""

    kind = "mock"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.sent: list[MidiEvent] = []

    async def open(self) -> None:
        self.set_status(True, "Mock Instrument")

    async def _deliver(self, event: MidiEvent) -> None:
        self.sent.append(event)

    @property
    def note_ons(self) -> list[MidiEvent]:
        return [e for e in self.sent if e.is_note_on]


def create_sink(settings: Settings) -> MidiSink:
    """Pick the best available output backend for this machine."""
    choice = (settings.midi_sink or "auto").lower()

    if choice == "mock":
        return MockSink(settings)
    if choice == "none":
        return NullSink(settings)
    if choice == "alsa":
        from app.midi.alsa_sink import AlsaSink
        return AlsaSink(settings)
    if choice == "portable":
        from app.midi.portable_sink import PortableSink
        return PortableSink(settings)

    if platform.system() == "Linux":
        try:
            from app.midi.alsa_sink import AlsaSink
            return AlsaSink(settings)
        except ImportError:
            log.warning("alsa-midi not installed; MIDI output unavailable")
    try:
        from app.midi.portable_sink import PortableSink
        return PortableSink(settings)
    except ImportError:
        pass

    log.info("No MIDI output backend on this machine; browser playback only")
    return NullSink(settings)
