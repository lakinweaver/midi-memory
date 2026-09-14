"""End-to-end on the client: a stream of MIDI in, a spooled session out."""
from __future__ import annotations

import asyncio
import time

import pytest

from midi_memory.client.capture import CaptureService
from midi_memory.client.midi.source import MidiSource
from midi_memory.client.spool import Spool
from midi_memory.shared.midi.events import MidiEvent


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
    spool = Spool(settings)
    source = ScriptedSource(settings)
    nudges = []
    service = CaptureService(settings, spool, source=source,
                             on_session=lambda record: nudges.append(record.id))
    return settings, spool, source, service, nudges


async def test_played_phrase_becomes_a_spooled_session(wired):
    settings, spool, source, service, nudges = wired
    await service.start()
    try:
        await source.play_phrase(notes=8)
        await asyncio.sleep(0.1)
        assert service.status()["recording"] is True
        assert service.status()["state"] == "recording"

        await asyncio.sleep(2.5)  # go quiet well past idle_seconds + one tick

        pending = spool.pending()
        assert len(pending) == 1
        payload = pending[0].payload()
        assert payload.note_count == 8
        assert payload.device_name == "Scripted Piano", "the take names its instrument"
        assert pending[0].midi_path.exists()
        assert pending[0].midi_path.stat().st_size > 0
        assert nudges == [payload.id], "the uploader is told to drain"
    finally:
        await service.stop()


async def test_two_phrases_separated_by_silence_become_two_sessions(wired):
    settings, spool, source, service, _ = wired
    await service.start()
    try:
        await source.play_phrase(notes=6, base=60)
        await asyncio.sleep(2.5)
        await source.play_phrase(notes=6, base=72)
        await asyncio.sleep(2.5)

        assert spool.pending_count() == 2
    finally:
        await service.stop()


async def test_pending_sessions_are_ordered_by_when_they_were_played(wired):
    settings, spool, source, service, _ = wired
    await service.start()
    try:
        await source.play_phrase(notes=5, base=48)
        await asyncio.sleep(2.5)
        await source.play_phrase(notes=5, base=72)
        await asyncio.sleep(2.5)

        first, second = spool.pending()
        assert first.payload().lowest_note < second.payload().lowest_note, (
            "the take played first must upload first"
        )
    finally:
        await service.stop()


async def test_shutdown_spools_the_session_in_progress(wired):
    """Turning the Pi off mid-idea must not throw the idea away."""
    settings, spool, source, service, _ = wired
    await service.start()
    await source.play_phrase(notes=9)
    await asyncio.sleep(0.1)
    assert spool.pending_count() == 0, "still recording, nothing finished yet"

    await service.stop()

    pending = spool.pending()
    assert len(pending) == 1
    assert pending[0].payload().note_count == 9


async def test_an_interrupted_session_is_recovered_on_the_next_start(wired):
    """The power cut case: events on disk, no MIDI file, no upload payload."""
    settings, spool, source, service, _ = wired
    await service.start()
    await source.play_phrase(notes=7)
    await asyncio.sleep(0.1)
    # Drop the service on the floor without letting it finalise anything.
    for task in service._tasks:
        task.cancel()
    service.recorder._pending.handle.flush()

    fresh = CaptureService(settings, spool, source=ScriptedSource(settings))
    await fresh.start()
    try:
        pending = spool.pending()
        assert len(pending) == 1
        assert pending[0].payload().note_count == 7
    finally:
        await fresh.stop()
