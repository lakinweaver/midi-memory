"""Standard MIDI File rendering, plus note extraction for the browser player."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import mido

from app.midi.events import (
    CC_ALL_NOTES_OFF,
    CC_SOSTENUTO,
    CC_SUSTAIN,
    CONTROL_CHANGE,
    MidiEvent,
    parse_bytes,
)

TICKS_PER_BEAT = 480
TEMPO_US_PER_BEAT = 500_000  # 120 bpm; a neutral grid, we record wall-clock not bars
TICKS_PER_SECOND = TICKS_PER_BEAT * 1_000_000 / TEMPO_US_PER_BEAT  # 960


def seconds_to_ticks(seconds: float) -> int:
    return max(0, round(seconds * TICKS_PER_SECOND))


def _to_mido(event: MidiEvent, delta_ticks: int) -> mido.Message | None:
    kind, ch = event.kind, event.channel
    try:
        if kind == 0x80:
            return mido.Message("note_off", channel=ch, note=event.data1,
                                velocity=event.data2, time=delta_ticks)
        if kind == 0x90:
            return mido.Message("note_on", channel=ch, note=event.data1,
                                velocity=event.data2, time=delta_ticks)
        if kind == 0xA0:
            return mido.Message("polytouch", channel=ch, note=event.data1,
                                value=event.data2, time=delta_ticks)
        if kind == 0xB0:
            return mido.Message("control_change", channel=ch, control=event.data1,
                                value=event.data2, time=delta_ticks)
        if kind == 0xC0:
            return mido.Message("program_change", channel=ch, program=event.data1,
                                time=delta_ticks)
        if kind == 0xD0:
            return mido.Message("aftertouch", channel=ch, value=event.data1,
                                time=delta_ticks)
        if kind == 0xE0:
            # 14-bit, centred on 0 in mido's representation
            value = ((event.data2 << 7) | event.data1) - 8192
            return mido.Message("pitchwheel", channel=ch, pitch=value, time=delta_ticks)
    except (ValueError, KeyError):
        return None
    return None


def write_smf(events: Sequence[MidiEvent], path: Path, name: str = "") -> None:
    """Render events (timestamps relative to session start) to a type-0 MIDI file."""
    mf = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mf.tracks.append(track)

    if name:
        track.append(mido.MetaMessage("track_name", name=name[:120], time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=TEMPO_US_PER_BEAT, time=0))

    prev_ticks = 0
    for event in events:
        abs_ticks = seconds_to_ticks(event.t)
        msg = _to_mido(event, max(0, abs_ticks - prev_ticks))
        if msg is None:
            continue
        track.append(msg)
        prev_ticks = abs_ticks

    track.append(mido.MetaMessage("end_of_track", time=1))
    path.parent.mkdir(parents=True, exist_ok=True)
    mf.save(path)


def read_smf(path: Path) -> list[MidiEvent]:
    """Read a MIDI file back into normalised events with absolute-second timestamps."""
    mf = mido.MidiFile(path)
    out: list[MidiEvent] = []
    t = 0.0
    for msg in mf:  # iterating MidiFile yields wall-clock deltas in seconds
        t += msg.time
        if msg.is_meta:
            continue
        raw = msg.bytes()
        event = parse_bytes(t, raw)
        if event is not None:
            out.append(event)
    return out


@dataclass(slots=True)
class Note:
    """One note, with two distinct lifetimes.

    `duration` is how long the key was actually held -- that is what a piano roll
    should draw, otherwise a pedalled passage becomes one unreadable smear.
    `release` is how long it kept sounding once the sustain pedal is taken into
    account -- that is what playback should use, because it is what you heard.
    """

    note: int
    start: float
    duration: float
    velocity: int
    release: float = 0.0

    def to_row(self) -> dict:
        return {
            "n": self.note,
            "s": round(self.start, 4),
            "d": round(self.duration, 4),
            "v": self.velocity,
            "r": round(max(self.duration, self.release), 4),
        }


def extract_notes(
    events: Iterable[MidiEvent],
    apply_sustain: bool = True,
    default_release: float = 0.35,
) -> list[Note]:
    """Pair note-ons with note-offs into playable notes.

    Each note gets both the time its key was held (`duration`) and the time it
    actually rang (`release`), which the sustain pedal can extend well past the
    key-up. Keeping them apart lets the piano roll stay legible while playback
    still sounds like the performance.
    """
    events = sorted(events, key=lambda e: e.t)
    open_notes: dict[tuple[int, int], Note] = {}
    held_by_pedal: list[Note] = []
    sustain_on = False
    notes: list[Note] = []
    last_t = 0.0

    def key_up(note_obj: Note, end: float) -> None:
        note_obj.duration = max(0.02, end - note_obj.start)
        note_obj.release = note_obj.duration

    def damp(note_obj: Note, end: float) -> None:
        note_obj.release = max(note_obj.duration, end - note_obj.start)

    for event in events:
        last_t = max(last_t, event.t)
        key = (event.channel, event.data1)

        if event.is_note_on:
            existing = open_notes.pop(key, None)
            if existing is not None:  # retrigger without an off
                key_up(existing, event.t)
            note_obj = Note(note=event.data1, start=event.t, duration=0.0,
                            velocity=event.data2)
            open_notes[key] = note_obj
            notes.append(note_obj)

        elif event.is_note_off:
            note_obj = open_notes.pop(key, None)
            if note_obj is None:
                continue
            key_up(note_obj, event.t)
            if apply_sustain and sustain_on:
                held_by_pedal.append(note_obj)

        elif event.kind == CONTROL_CHANGE and event.data1 in (CC_SUSTAIN, CC_SOSTENUTO):
            if event.is_sustain_down:
                sustain_on = True
            else:
                sustain_on = False
                for note_obj in held_by_pedal:
                    damp(note_obj, event.t)
                held_by_pedal.clear()

        elif event.kind == CONTROL_CHANGE and event.data1 == CC_ALL_NOTES_OFF:
            for note_obj in list(open_notes.values()):
                key_up(note_obj, event.t)
            for note_obj in held_by_pedal:
                damp(note_obj, event.t)
            open_notes.clear()
            held_by_pedal.clear()
            sustain_on = False

    # Anything still down when the recording stopped gets a natural release.
    for note_obj in open_notes.values():
        key_up(note_obj, last_t + default_release)
    for note_obj in held_by_pedal:
        damp(note_obj, last_t + default_release)

    notes.sort(key=lambda n: (n.start, n.note))
    return notes


FINGERPRINT_POINTS = 64


def fingerprint(notes: Sequence[Note], max_points: int = FINGERPRINT_POINTS) -> list[list[int]]:
    """A tiny sketch of a performance, for the library's pitch strips.

    Each entry is [start, note, length, velocity] with time quantised to 0-255
    across the session -- finer than the ~96px the strip is drawn at. Storing
    this once means the library page needs a single request instead of one per
    row to fetch full note lists it only ever draws as a thumbnail.
    """
    if not notes:
        return []

    end = max(n.start + n.duration for n in notes) or 1.0

    # Take the loudest note from each of `max_points` time buckets: even coverage
    # across the session, keeping whatever is most visually prominent in each.
    buckets: dict[int, Note] = {}
    for note in notes:
        slot = min(max_points - 1, int(note.start / end * max_points))
        current = buckets.get(slot)
        if current is None or note.velocity > current.velocity:
            buckets[slot] = note

    out: list[list[int]] = []
    for _, note in sorted(buckets.items()):
        start = min(255, max(0, round(note.start / end * 255)))
        length = min(255, max(1, round(note.duration / end * 255)))
        out.append([start, note.note, length, note.velocity])
    return out


def compute_stats(events: Sequence[MidiEvent]) -> dict:
    """Summary figures stored alongside each session for display and filtering."""
    note_ons = [e for e in events if e.is_note_on]
    pitches = [e.data1 for e in note_ons]
    velocities = [e.data2 for e in note_ons]
    duration = (events[-1].t - events[0].t) if events else 0.0
    return {
        "event_count": len(events),
        "note_count": len(note_ons),
        "duration_ms": int(round(duration * 1000)),
        "lowest_note": min(pitches) if pitches else None,
        "highest_note": max(pitches) if pitches else None,
        "avg_velocity": (sum(velocities) / len(velocities)) if velocities else None,
    }
