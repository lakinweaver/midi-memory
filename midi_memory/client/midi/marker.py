"""The session-break gesture: two quick presses of one key end the take.

Silence is otherwise the only thing that ends a session, so finishing one idea and
starting a different one inside the idle window files them as a single recording.
This gives that moment a deliberate signal -- press the top key of the keyboard
twice, at the pace of a double click, and the take closes where you meant it to.

The two presses are a gesture rather than music, so they must never reach the
event log. Since the recorder flushes every event to disk the moment it arrives,
the only way to keep them out is to decide before they get there: a press is held
back until it is clear which it was. A second press inside the window discards the
pair; the window passing releases the first press into the recording as the
ordinary note it turned out to be.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from midi_memory.client.config import Settings
from midi_memory.shared.midi.events import NOTE_OFF, NOTE_ON, POLY_AFTERTOUCH, MidiEvent

log = logging.getLogger(__name__)

# Messages addressed to one particular key, and so to the marker key when the note
# number matches. Sustain, bend and the rest say nothing about which key is down.
_KEY_KINDS = frozenset({NOTE_ON, NOTE_OFF, POLY_AFTERTOUCH})

# A suppressed press whose note-off never arrives -- a cable pulled mid-gesture --
# would otherwise leave the key swallowed for the life of the process.
SUPPRESS_CEILING_SECONDS = 30.0


@dataclass(slots=True, frozen=True)
class Decision:
    """What the listener wants done with the stream, in this order."""

    record: tuple[MidiEvent, ...] = ()
    end_session: bool = False


_NOTHING = Decision()


class MarkerListener:
    """Watches for the double-press and holds back the keystrokes that make it.

    Settings are read live rather than copied in, so changing the key or the
    window on the settings page takes effect on the next note, not the next
    restart -- the same bargain every other capture setting makes.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._armed_at: Optional[float] = None
        self._buffer: list[MidiEvent] = []
        # Per channel, how many marker presses we dropped that are still down.
        # Their note-offs have to be dropped alongside them, or a lone note-off
        # lands in the recording -- and, worse, opens the next session.
        self._suppressed: dict[int, int] = {}
        self._suppressed_at = 0.0

    # -- ingestion -----------------------------------------------------------
    def feed(self, event: MidiEvent) -> Decision:
        """Judge one event on its way to the recorder."""
        if not self.settings.marker_enabled:
            return Decision(self._release() + (event,))
        if not self._is_marker(event):
            # Anything else ends the gesture: two presses with a note in between
            # are two notes, not a double press.
            return Decision(self._release() + (event,))

        channel = event.channel

        # The tail of a gesture already acted on: a finger still on the key.
        if channel in self._suppressed:
            if event.is_note_off:
                self._forget(channel)
            return _NOTHING

        if event.is_note_on:
            if self._armed_at is not None and event.t - self._armed_at <= self._window:
                return self._trigger(event)
            released = self._release()
            self._armed_at = event.t
            self._buffer.append(event)
            return Decision(released)

        # A note-off or aftertouch belonging to the press we are holding travels
        # with it, so the pair is released or discarded together.
        if self._armed_at is not None:
            self._buffer.append(event)
            return _NOTHING

        return Decision((event,))

    def expire(self, now: float) -> tuple[MidiEvent, ...]:
        """Called on the recorder's tick: releases a press once its window passed."""
        if self._suppressed and now - self._suppressed_at > SUPPRESS_CEILING_SECONDS:
            log.warning("Marker key held %.0fs with no note-off; releasing it",
                        now - self._suppressed_at)
            self._suppressed.clear()
        if self._armed_at is None:
            return ()
        if self.settings.marker_enabled and now - self._armed_at <= self._window:
            return ()
        return self._release()

    def drain(self) -> tuple[MidiEvent, ...]:
        """Give up whatever is held. For shutdown, where nothing more is coming."""
        return self._release()

    # -- internals -----------------------------------------------------------
    @property
    def _window(self) -> float:
        return float(self.settings.marker_double_press_seconds)

    def _is_marker(self, event: MidiEvent) -> bool:
        return event.kind in _KEY_KINDS and event.data1 == self.settings.marker_note

    def _trigger(self, event: MidiEvent) -> Decision:
        """Both presses are the gesture: drop them and ask for the session to end."""
        self._suppressed = self._still_down(self._buffer)
        self._suppressed[event.channel] = self._suppressed.get(event.channel, 0) + 1
        self._suppressed_at = event.t
        self._buffer.clear()
        self._armed_at = None
        return Decision(end_session=True)

    def _release(self) -> tuple[MidiEvent, ...]:
        """Hand back the held press: it was a note after all."""
        if not self._buffer:
            return ()
        held = tuple(self._buffer)
        self._buffer.clear()
        self._armed_at = None
        return held

    def _forget(self, channel: int) -> None:
        remaining = self._suppressed[channel] - 1
        if remaining > 0:
            self._suppressed[channel] = remaining
        else:
            del self._suppressed[channel]

    @staticmethod
    def _still_down(events: Iterable[MidiEvent]) -> dict[int, int]:
        held: dict[int, int] = {}
        for event in events:
            if event.is_note_on:
                held[event.channel] = held.get(event.channel, 0) + 1
            elif event.is_note_off and held.get(event.channel):
                held[event.channel] -= 1
                if not held[event.channel]:
                    del held[event.channel]
        return held
