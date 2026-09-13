"""Segmentation is the one piece that silently ruins the app if it's wrong."""
from __future__ import annotations

import json

from app.midi.events import MidiEvent, parse_bytes
from app.midi.recorder import EVENTS_FILENAME, Recorder, recover_orphans


def note_on(t, note=60, vel=90):
    return MidiEvent(t, 0x90, note, vel)


def note_off(t, note=60):
    return MidiEvent(t, 0x80, note, 0)


def play_phrase(recorder, clock, notes=6, gap=0.4, base=60):
    """Play a short run of notes, advancing the clock as a human would."""
    for i in range(notes):
        recorder.handle(note_on(clock.now, base + i))
        clock.advance(gap / 2)
        recorder.handle(note_off(clock.now, base + i))
        clock.advance(gap / 2)


def test_idle_gap_splits_into_two_sessions(settings, clock):
    saved = []
    rec = Recorder(settings, clock=clock, on_finalized=saved.append)

    play_phrase(rec, clock)
    assert rec.is_recording

    clock.advance(10)
    assert rec.tick() is None, "10s of silence is a pause, not the end of an idea"

    clock.advance(40)  # now past the 45s threshold
    assert rec.tick() is not None
    assert not rec.is_recording

    play_phrase(rec, clock)
    clock.advance(50)
    rec.tick()

    assert len(saved) == 2
    assert saved[0].id != saved[1].id
    assert all(s.note_count == 6 for s in saved)


def test_session_does_not_end_while_a_note_is_held(settings, clock):
    saved = []
    rec = Recorder(settings, clock=clock, on_finalized=saved.append)

    play_phrase(rec, clock)
    rec.handle(note_on(clock.now, 72))  # final note, still held

    clock.advance(120)
    assert rec.tick() is None, "a held chord must not be guillotined"

    rec.handle(note_off(clock.now, 72))
    clock.advance(50)
    assert rec.tick() is not None
    assert len(saved) == 1


def test_sustain_pedal_also_holds_the_session_open(settings, clock):
    saved = []
    rec = Recorder(settings, clock=clock, on_finalized=saved.append)

    play_phrase(rec, clock)
    rec.handle(MidiEvent(clock.now, 0xB0, 64, 127))  # pedal down

    clock.advance(100)
    assert rec.tick() is None

    rec.handle(MidiEvent(clock.now, 0xB0, 64, 0))  # pedal up
    clock.advance(50)
    assert rec.tick() is not None


def test_stuck_note_is_force_closed_at_the_hard_ceiling(settings, clock):
    """An unplugged cable mid-note must not wedge the recorder forever."""
    saved = []
    rec = Recorder(settings, clock=clock, on_finalized=saved.append)

    play_phrase(rec, clock)
    rec.handle(note_on(clock.now, 80))  # never released

    clock.advance(settings.idle_seconds * 4 + 1)
    assert rec.tick() is not None
    assert not rec.is_recording


def test_accidental_keypress_is_discarded(settings, clock):
    saved = []
    rec = Recorder(settings, clock=clock, on_finalized=saved.append)

    rec.handle(note_on(clock.now, 60))
    clock.advance(0.2)
    rec.handle(note_off(clock.now, 60))

    clock.advance(60)
    assert rec.tick() is None, "one brushed key is not an idea"
    assert saved == []
    assert list(settings.sessions_dir.iterdir()) == [], "its directory is cleaned up too"


def test_active_sensing_and_clock_are_never_recordable():
    """Pianos emit these constantly; counting them would mean a session never ends."""
    assert parse_bytes(0.0, b"\xfe") is None  # active sensing
    assert parse_bytes(0.0, b"\xf8") is None  # timing clock
    assert parse_bytes(0.0, b"\xfa") is None  # start
    assert parse_bytes(0.0, b"\x90\x3c\x64") is not None


def test_events_are_on_disk_before_the_session_ends(settings, clock):
    """A power cut must not lose the notes already played."""
    rec = Recorder(settings, clock=clock)
    play_phrase(rec, clock, notes=5)

    directory = settings.sessions_dir / rec.current_id
    lines = (directory / EVENTS_FILENAME).read_text().strip().splitlines()
    assert len(lines) == 10  # 5 note-ons + 5 note-offs, already flushed
    assert json.loads(lines[0])["s"] == 0x90


def test_crash_recovery_finalizes_an_interrupted_session(settings, clock):
    rec = Recorder(settings, clock=clock)
    play_phrase(rec, clock, notes=8)
    session_id = rec.current_id
    rec._pending.handle.flush()  # simulate power loss: never finalized

    recovered = recover_orphans(settings, known_ids=set())

    assert len(recovered) == 1
    assert recovered[0].id == session_id
    assert recovered[0].note_count == 8
    assert recovered[0].midi_path.exists()


def test_recovery_skips_sessions_already_in_the_database(settings, clock):
    rec = Recorder(settings, clock=clock)
    play_phrase(rec, clock, notes=8)
    session_id = rec.current_id

    assert recover_orphans(settings, known_ids={session_id}) == []


def test_timestamps_are_relative_to_session_start(settings, clock):
    """Sessions recorded days apart must both start at t=0 in their MIDI file."""
    clock.advance(86_400)
    rec = Recorder(settings, clock=clock)
    play_phrase(rec, clock, notes=5)
    assert rec._pending.events[0].t == 0.0
