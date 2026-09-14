"""The session-break gesture: two quick presses of one key end the take.

These drive the real CaptureService wiring rather than the listener alone, because
the thing worth protecting is the join: a gesture that ends a session but leaves
its own keystrokes in the recording would be worse than no gesture at all.
"""
from __future__ import annotations

import json

import pytest

from midi_memory.client.capture import CaptureService
from midi_memory.client.midi.marker import SUPPRESS_CEILING_SECONDS, Decision
from midi_memory.client.midi.recorder import EVENTS_FILENAME, Recorder
from midi_memory.shared.midi.events import MidiEvent

MARKER = 108  # C8, the default: the top key of an 88


@pytest.fixture
def service(settings, spool, clock):
    svc = CaptureService(settings, spool)
    # The live service times off time.monotonic(). Everything under test reads
    # the timestamp on the event instead, so the recorder can run on the fake
    # clock and leave the tests in charge of how fast a gesture happened.
    svc.recorder = Recorder(settings, clock=clock, on_finalized=svc._on_finalized)
    return svc


def note_on(t, note, vel=90):
    return MidiEvent(t, 0x90, note, vel)


def note_off(t, note):
    return MidiEvent(t, 0x80, note, 0)


def feed(service, event):
    service._dispatch(service.marker.feed(event))


def tick(service, clock):
    """What the service's one-second loop does with the marker."""
    service._dispatch(Decision(service.marker.expire(clock.now)))


def play_phrase(service, clock, notes=6, base=60):
    for i in range(notes):
        feed(service, note_on(clock.now, base + i))
        clock.advance(0.2)
        feed(service, note_off(clock.now, base + i))
        clock.advance(0.2)


def press(service, clock, note=MARKER, hold=0.06):
    feed(service, note_on(clock.now, note))
    clock.advance(hold)
    feed(service, note_off(clock.now, note))


def logged_notes(directory):
    """Every note number in a session's append log, gesture leakage included."""
    lines = (directory / EVENTS_FILENAME).read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line)["a"] for line in lines if line]


def test_double_press_ends_the_session(service, clock):
    play_phrase(service, clock)
    assert service.recorder.is_recording

    press(service, clock)
    clock.advance(0.15)
    press(service, clock)

    assert not service.recorder.is_recording, "the gesture closes the take"
    assert service.marker_ended_at is not None


def test_neither_press_reaches_the_recording(service, clock):
    finished = []
    service.recorder.on_finalized = lambda r: finished.append(r)

    play_phrase(service, clock)
    press(service, clock)
    clock.advance(0.15)
    press(service, clock)

    assert MARKER not in logged_notes(finished[0].directory)
    assert finished[0].highest_note == 65, "the take ends on the music, not the marker"


def test_the_gesture_does_not_leak_into_the_next_session(service, clock):
    """The second press's note-off arrives after the session has already closed.

    Left alone it would not just appear in the next take -- it would *open* it,
    since the recorder starts a session on whatever event it sees first.
    """
    play_phrase(service, clock)
    feed(service, note_on(clock.now, MARKER))
    clock.advance(0.06)
    feed(service, note_off(clock.now, MARKER))
    clock.advance(0.15)
    feed(service, note_on(clock.now, MARKER))   # trigger: the session ends here
    clock.advance(0.06)
    feed(service, note_off(clock.now, MARKER))  # the tail, after the cut

    assert not service.recorder.is_recording, "a stray note-off must not open a take"

    clock.advance(5)
    play_phrase(service, clock, base=48)
    assert MARKER not in logged_notes(service.recorder._pending.directory)


def test_the_next_idea_is_a_separate_session(service, clock):
    play_phrase(service, clock)
    first = service.recorder.current_id

    press(service, clock)
    clock.advance(0.15)
    press(service, clock)

    clock.advance(2)
    play_phrase(service, clock, base=48)
    assert service.recorder.is_recording
    assert service.recorder.current_id != first


def test_presses_too_far_apart_are_music(service, clock):
    """Two presses at a slow pace are two notes, and both belong in the take."""
    play_phrase(service, clock)

    press(service, clock)
    clock.advance(1.5)
    press(service, clock)
    clock.advance(1.5)
    tick(service, clock)

    assert service.recorder.is_recording, "a slow pair is not the gesture"
    assert logged_notes(service.recorder._pending.directory).count(MARKER) == 4


def test_a_single_press_is_released_into_the_recording(service, clock):
    play_phrase(service, clock)
    press(service, clock)

    assert MARKER not in logged_notes(service.recorder._pending.directory), \
        "held back until it is clear which it was"

    clock.advance(1.0)
    tick(service, clock)
    assert logged_notes(service.recorder._pending.directory).count(MARKER) == 2


def test_holding_the_key_is_not_a_double_press(service, clock):
    """Press-hold-release-press spans the window; only the pace of a click counts."""
    play_phrase(service, clock)

    feed(service, note_on(clock.now, MARKER))
    clock.advance(2.0)
    feed(service, note_off(clock.now, MARKER))
    clock.advance(0.05)
    feed(service, note_on(clock.now, MARKER))
    clock.advance(0.06)
    feed(service, note_off(clock.now, MARKER))

    assert service.recorder.is_recording


def test_a_note_in_between_breaks_the_gesture(service, clock):
    play_phrase(service, clock)

    press(service, clock)
    clock.advance(0.05)
    press(service, clock, note=60)   # an ordinary note, mid-gesture
    clock.advance(0.05)
    press(service, clock)

    assert service.recorder.is_recording
    clock.advance(1.0)
    tick(service, clock)
    assert logged_notes(service.recorder._pending.directory).count(MARKER) == 4


def test_double_press_while_idle_does_nothing(service, clock, settings):
    press(service, clock)
    clock.advance(0.15)
    press(service, clock)

    assert not service.recorder.is_recording
    assert list(settings.spool_dir.iterdir()) == [], "no session was opened to end"
    assert service.marker_ended_at is not None, "but the page still confirms it landed"


def test_the_key_is_ordinary_music_when_the_gesture_is_off(service, clock, settings):
    settings.marker_enabled = False
    play_phrase(service, clock)

    press(service, clock)
    clock.advance(0.15)
    press(service, clock)

    assert service.recorder.is_recording
    assert logged_notes(service.recorder._pending.directory).count(MARKER) == 4


def test_the_configured_key_is_the_one_that_counts(service, clock, settings):
    settings.marker_note = 21   # A0, the bottom of an 88
    play_phrase(service, clock)

    press(service, clock, note=MARKER)
    clock.advance(0.15)
    press(service, clock, note=MARKER)
    assert service.recorder.is_recording, "108 is no longer the marker"

    press(service, clock, note=21)
    clock.advance(0.15)
    press(service, clock, note=21)
    assert not service.recorder.is_recording


def test_a_marker_press_with_no_note_off_is_released_at_the_ceiling(service, clock):
    """An unplugged cable mid-gesture must not swallow the key for good."""
    play_phrase(service, clock)
    feed(service, note_on(clock.now, MARKER))
    clock.advance(0.15)
    feed(service, note_on(clock.now, MARKER))   # trigger; no note-off ever comes
    assert not service.recorder.is_recording

    clock.advance(SUPPRESS_CEILING_SECONDS + 1)
    tick(service, clock)

    play_phrase(service, clock, base=48)
    press(service, clock)
    clock.advance(0.15)
    press(service, clock)
    assert not service.recorder.is_recording, "the gesture works again"


def test_a_press_still_being_decided_is_kept_at_shutdown(service, clock):
    play_phrase(service, clock)
    feed(service, note_on(clock.now, MARKER))
    clock.advance(0.06)
    feed(service, note_off(clock.now, MARKER))

    directory = service.recorder._pending.directory
    service._dispatch(Decision(service.marker.drain()))

    assert logged_notes(directory).count(MARKER) == 2, "not lost on the way down"
