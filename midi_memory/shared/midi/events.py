"""Normalised MIDI event representation shared by every input source.

Sources speak different dialects (ALSA sequencer objects, rtmidi raw bytes, synthetic
test data), so they all convert to this one small struct before reaching the recorder.
"""
from __future__ import annotations

from dataclasses import dataclass

# Channel-voice status nibbles
NOTE_OFF = 0x80
NOTE_ON = 0x90
POLY_AFTERTOUCH = 0xA0
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
CHANNEL_AFTERTOUCH = 0xD0
PITCH_BEND = 0xE0

# Control numbers we care about
CC_SUSTAIN = 64
CC_SOSTENUTO = 66
CC_ALL_NOTES_OFF = 123

# System realtime / common bytes that must never count as musical activity.
# Most digital pianos emit Active Sensing every ~300ms; if that reset the idle
# timer, a session would never end.
IGNORED_STATUS = frozenset({
    0xF0,  # sysex start
    0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
    0xF8,  # timing clock
    0xF9, 0xFA, 0xFB, 0xFC, 0xFD,
    0xFE,  # active sensing
    0xFF,  # system reset
})

# Status bytes that carry only one data byte.
_ONE_DATA_BYTE = frozenset({PROGRAM_CHANGE, CHANNEL_AFTERTOUCH})


@dataclass(slots=True, frozen=True)
class MidiEvent:
    """A single channel-voice message, timestamped on the recorder's clock."""

    t: float  # monotonic seconds, as observed by the capture layer
    status: int  # full status byte, channel included
    data1: int = 0
    data2: int = 0

    # -- classification ------------------------------------------------------
    @property
    def kind(self) -> int:
        """Status nibble with the channel masked off."""
        return self.status & 0xF0

    @property
    def channel(self) -> int:
        return self.status & 0x0F

    @property
    def is_note_on(self) -> bool:
        # A note_on with zero velocity is a note_off by convention, and many
        # keyboards use it exclusively (running status saves a byte).
        return self.kind == NOTE_ON and self.data2 > 0

    @property
    def is_note_off(self) -> bool:
        return self.kind == NOTE_OFF or (self.kind == NOTE_ON and self.data2 == 0)

    @property
    def is_sustain_down(self) -> bool:
        return (
            self.kind == CONTROL_CHANGE
            and self.data1 in (CC_SUSTAIN, CC_SOSTENUTO)
            and self.data2 >= 64
        )

    @property
    def is_sustain_up(self) -> bool:
        return (
            self.kind == CONTROL_CHANGE
            and self.data1 in (CC_SUSTAIN, CC_SOSTENUTO)
            and self.data2 < 64
        )

    # -- serialisation -------------------------------------------------------
    def to_row(self) -> dict:
        """Compact JSON form for the per-session append log."""
        return {"t": round(self.t, 6), "s": self.status, "a": self.data1, "b": self.data2}

    @classmethod
    def from_row(cls, row: dict) -> "MidiEvent":
        return cls(t=row["t"], status=row["s"], data1=row.get("a", 0), data2=row.get("b", 0))

    def shifted(self, offset: float) -> "MidiEvent":
        return MidiEvent(self.t - offset, self.status, self.data1, self.data2)


def is_recordable(status: int) -> bool:
    """True for channel-voice messages worth recording and counting as activity."""
    if status in IGNORED_STATUS or status < 0x80:
        return False
    return (status & 0xF0) != 0xF0


def parse_bytes(t: float, data: bytes | bytearray | list[int]) -> MidiEvent | None:
    """Build an event from raw MIDI bytes, or None if it isn't a recordable message."""
    if not data:
        return None
    status = data[0]
    if not is_recordable(status):
        return None
    expected = 2 if (status & 0xF0) in _ONE_DATA_BYTE else 3
    if len(data) < expected:
        return None
    d1 = data[1] & 0x7F
    d2 = (data[2] & 0x7F) if expected == 3 else 0
    return MidiEvent(t=t, status=status, data1=d1, data2=d2)
