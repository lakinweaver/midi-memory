"""End-to-end: a stream of MIDI in, a saved, searchable session out."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.db import Database
from app.events import EventBus
from app.midi.events import MidiEvent
from app.midi.sink import MockSink as MockSinkBase
from app.midi.source import MidiSource
from app.service import CaptureService


class ScriptedSource(MidiSource):
    """Plays a fixed phrase on demand so the test controls the timing."""

    kind = "scripted"

    async def run(self) -> None:
        self.set_status(True, "Scripted Piano")
        while not self._closing:
            await asyncio.sleep(0.05)

    async def play_phrase(self, notes=8, base=60, step=0.05) -> None:
        """Emit in real time with present-tense timestamps, as a real source does."""
        for i in range(notes):
            self.emit(MidiEvent(time.monotonic(), 0x90, base + i, 90))
            await asyncio.sleep(step)
            self.emit(MidiEvent(time.monotonic(), 0x80, base + i, 0))


@pytest.fixture
def wired(settings):
    settings.idle_seconds = 0.5
    settings.min_seconds = 0.1
    settings.min_notes = 4
    db = Database(settings.db_path)
    bus = EventBus()
    source = ScriptedSource(settings)
    return settings, db, bus, source, CaptureService(settings, db, bus, source=source)


async def test_played_phrase_becomes_a_saved_session(wired):
    settings, db, bus, source, service = wired
    await service.start()
    try:
        await source.play_phrase(notes=8)
        await asyncio.sleep(0.1)
        assert service.status()["recording"] is True

        await asyncio.sleep(2.5)  # go quiet well past idle_seconds + one tick

        result = db.search()
        assert result["total"] == 1
        session = result["items"][0]
        assert session["note_count"] == 8
        assert session["name"], "a session gets a human-readable default name"

        midi_file = settings.sessions_dir / session["id"] / "session.mid"
        assert midi_file.exists() and midi_file.stat().st_size > 0
    finally:
        await service.stop()


async def test_two_phrases_separated_by_silence_become_two_sessions(wired):
    settings, db, bus, source, service = wired
    await service.start()
    try:
        await source.play_phrase(notes=6, base=60)
        await asyncio.sleep(2.5)
        await source.play_phrase(notes=6, base=72)
        await asyncio.sleep(2.5)

        assert db.search()["total"] == 2
    finally:
        await service.stop()


async def test_live_events_reach_the_browser_bus(wired):
    settings, db, bus, source, service = wired
    await service.start()
    try:
        with bus.subscribe() as queue:
            await source.play_phrase(notes=5)
            await asyncio.sleep(0.1)

            seen = []
            while not queue.empty():
                seen.append(queue.get_nowait())
            types = {m["type"] for m in seen}
            assert "session_started" in types
            assert "activity" in types

            await asyncio.sleep(2.5)
            while not queue.empty():
                message = queue.get_nowait()
                if message["type"] == "session_saved":
                    assert message["session"]["note_count"] == 5
                    break
            else:
                pytest.fail("no session_saved event was published")
    finally:
        await service.stop()


async def test_shutdown_saves_the_session_in_progress(wired):
    """Turning the Pi off mid-idea must not throw the idea away."""
    settings, db, bus, source, service = wired
    await service.start()
    await source.play_phrase(notes=9)
    await asyncio.sleep(0.1)
    assert db.search()["total"] == 0, "still recording, nothing saved yet"

    await service.stop()

    assert db.search()["total"] == 1
    assert db.search()["items"][0]["note_count"] == 9


class EchoingSink(MockSinkBase):
    """A piano that echoes MIDI-in straight back out of MIDI-out.

    Plenty of real instruments do this, and it is the failure mode that would
    silently fill the library with recordings of its own playback.
    """

    def __init__(self, settings, source):
        super().__init__(settings)
        self.source = source

    async def _deliver(self, event):
        await super()._deliver(event)
        self.source.emit(MidiEvent(time.monotonic(), event.status, event.data1, event.data2))


async def test_device_playback_is_not_recorded_back(settings):
    """Playing a session to the piano must not create a new session."""
    settings.idle_seconds = 0.5
    settings.min_seconds = 0.1
    db = Database(settings.db_path)
    bus = EventBus()
    source = ScriptedSource(settings)

    sink = EchoingSink(settings, source)
    service = CaptureService(settings, db, bus, source=source, sink=sink)
    await service.start()
    try:
        # A real recording to play back.
        await source.play_phrase(notes=6)
        await asyncio.sleep(2.5)
        assert db.search()["total"] == 1
        session_id = db.search()["items"][0]["id"]

        await service.player.play(session_id)
        await asyncio.sleep(1.0)
        assert sink.sent, "the instrument should have received the performance"

        await service.player.stop()
        await asyncio.sleep(2.5)   # let any phantom session idle out

        assert db.search()["total"] == 1, (
            "the echoed playback was recorded as a new session - "
            "this is the feedback loop the suppression exists to prevent"
        )
    finally:
        await service.stop()


async def test_capture_can_be_re_enabled_for_playing_along(settings):
    """Opting in restores capture, for instruments that do not echo."""
    settings.capture_during_playback = True
    db = Database(settings.db_path)
    source = ScriptedSource(settings)
    sink = MockSinkBase(settings)
    service = CaptureService(settings, db, EventBus(), source=source, sink=sink)

    assert service._suppress_capture() is False
