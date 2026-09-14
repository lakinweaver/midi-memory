"""MIDI file rendering, note extraction, and the sustain-pedal distinction."""
from __future__ import annotations

from midi_memory.shared.midi.events import MidiEvent
from midi_memory.shared.midi.smf import compute_stats, extract_notes, read_smf, write_smf


def test_midi_file_round_trips(tmp_path):
    events = [
        MidiEvent(0.0, 0x90, 60, 100), MidiEvent(0.5, 0x80, 60, 0),
        MidiEvent(0.5, 0xB0, 64, 127), MidiEvent(1.5, 0xB0, 64, 0),
        MidiEvent(2.0, 0xE0, 0, 96),
    ]
    path = tmp_path / "s.mid"
    write_smf(events, path, name="Take one")

    assert path.read_bytes()[:4] == b"MThd"
    back = read_smf(path)
    assert len(back) == len(events)
    assert [e.status for e in back] == [e.status for e in events]
    assert back[1].t == 0.5


def test_key_duration_and_ring_time_are_separate():
    """The roll draws the key press; playback uses how long it actually rang."""
    events = [
        MidiEvent(0.0, 0xB0, 64, 127),                              # pedal down
        MidiEvent(0.2, 0x90, 60, 100), MidiEvent(0.4, 0x80, 60, 0),  # brief press
        MidiEvent(3.0, 0xB0, 64, 0),                                # pedal up
    ]
    note = extract_notes(events)[0]

    assert round(note.duration, 2) == 0.2, "key was only held 0.2s"
    assert round(note.release, 2) == 2.8, "but it rang until the pedal lifted"
    row = note.to_row()
    assert row["d"] == 0.2 and row["r"] == 2.8


def test_without_the_pedal_the_two_agree():
    events = [MidiEvent(0.0, 0x90, 60, 100), MidiEvent(1.0, 0x80, 60, 0)]
    note = extract_notes(events)[0]
    assert note.duration == note.release == 1.0


def test_note_on_with_zero_velocity_ends_the_note():
    events = [MidiEvent(0.0, 0x90, 60, 100), MidiEvent(0.8, 0x90, 60, 0)]
    notes = extract_notes(events)
    assert len(notes) == 1 and round(notes[0].duration, 2) == 0.8


def test_retriggering_without_a_note_off_still_closes_the_first():
    events = [MidiEvent(0.0, 0x90, 60, 90), MidiEvent(0.5, 0x90, 60, 90),
              MidiEvent(1.0, 0x80, 60, 0)]
    notes = extract_notes(events)
    assert len(notes) == 2
    assert round(notes[0].duration, 2) == 0.5


def test_a_note_left_held_at_the_end_still_gets_a_duration():
    """An unplugged cable must not produce a zero-length or infinite note."""
    notes = extract_notes([MidiEvent(0.0, 0x90, 60, 100)])
    assert len(notes) == 1
    assert 0 < notes[0].duration < 1.0


def test_all_notes_off_closes_everything():
    events = [MidiEvent(0.0, 0x90, 60, 90), MidiEvent(0.0, 0x90, 64, 90),
              MidiEvent(1.0, 0xB0, 123, 0)]
    notes = extract_notes(events)
    assert all(round(n.duration, 2) == 1.0 for n in notes)


def test_stats_summarise_the_performance():
    events = [
        MidiEvent(0.0, 0x90, 48, 60), MidiEvent(0.5, 0x80, 48, 0),
        MidiEvent(1.0, 0x90, 72, 100), MidiEvent(2.0, 0x80, 72, 0),
    ]
    stats = compute_stats(events)
    assert stats["note_count"] == 2
    assert stats["lowest_note"] == 48 and stats["highest_note"] == 72
    assert stats["avg_velocity"] == 80.0
    assert stats["duration_ms"] == 2000


def test_stats_on_an_empty_recording_do_not_explode():
    stats = compute_stats([])
    assert stats["note_count"] == 0 and stats["lowest_note"] is None


def test_fingerprint_is_small_and_ordered():
    """The library sends one of these per row, so it has to stay compact."""
    from midi_memory.shared.midi.smf import fingerprint

    events = []
    for i in range(400):                       # a long, dense take
        events.append(MidiEvent(i * 0.1, 0x90, 48 + (i % 36), 60 + (i % 60)))
        events.append(MidiEvent(i * 0.1 + 0.08, 0x80, 48 + (i % 36), 0))

    points = fingerprint(extract_notes(events))
    assert 0 < len(points) <= 64, "dense takes are downsampled for the thumbnail"
    assert points == sorted(points, key=lambda p: p[0]), "must be in time order"
    for start, note, length, velocity in points:
        assert 0 <= start <= 255 and 1 <= length <= 255
        assert 0 <= note <= 127 and 0 <= velocity <= 127


def test_fingerprint_keeps_every_note_of_a_short_take():
    from midi_memory.shared.midi.smf import fingerprint

    events = [MidiEvent(0.0, 0x90, 60, 90), MidiEvent(0.5, 0x80, 60, 0),
              MidiEvent(1.0, 0x90, 67, 80), MidiEvent(1.5, 0x80, 67, 0)]
    assert len(fingerprint(extract_notes(events))) == 2


def test_fingerprint_of_silence_is_empty():
    from midi_memory.shared.midi.smf import fingerprint
    assert fingerprint([]) == []
