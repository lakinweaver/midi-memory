"""Playback out to the instrument: timing, transport, and never leaving it ringing."""
from __future__ import annotations

import asyncio

import pytest

from app.midi.device_player import DevicePlayer, PlaybackUnavailable
from app.midi.events import MidiEvent
from app.midi.sink import MockSink, NullSink


def phrase(count: int = 4, step: float = 0.05) -> list[MidiEvent]:
    events: list[MidiEvent] = []
    for i in range(count):
        events.append(MidiEvent(i * step, 0x90, 60 + i, 90))
        events.append(MidiEvent(i * step + step / 2, 0x80, 60 + i, 0))
    return events


@pytest.fixture
async def rig(settings):
    sink = MockSink(settings)
    await sink.open()
    events = phrase()
    player = DevicePlayer(sink, load_events=lambda _sid: events)
    yield sink, player, events
    await player.stop()


async def test_plays_every_event_in_order(rig):
    sink, player, events = rig
    await player.play("s1", speed=2.0)
    await asyncio.sleep(0.6)

    assert not player.playing, "playback should have finished"
    assert [(e.status, e.data1) for e in sink.sent[:len(events)]] == \
           [(e.status, e.data1) for e in events]


async def test_position_advances_and_reports_duration(rig):
    sink, player, events = rig
    status = await player.play("s1")
    assert status["playing"] is True
    assert status["duration"] == pytest.approx(events[-1].t, abs=0.001)

    await asyncio.sleep(0.08)
    assert 0 < player.position <= player.duration


async def test_stopping_mid_phrase_silences_the_instrument(rig):
    """A cut stream must not leave the piano sustaining a chord forever."""
    sink = MockSink(rig[0].settings)
    await sink.open()
    held = [MidiEvent(0.0, 0x90, 60, 100), MidiEvent(0.0, 0xB0, 64, 127),
            MidiEvent(30.0, 0x80, 60, 0)]
    player = DevicePlayer(sink, load_events=lambda _s: held)

    await player.play("s1")
    await asyncio.sleep(0.1)          # note on + pedal down have gone out
    await player.stop()

    tail = sink.sent[2:]
    assert any(e.is_note_off and e.data1 == 60 for e in tail), "the note must be released"
    assert any(e.is_sustain_up for e in tail), "the pedal must be lifted"
    assert any(e.kind == 0xB0 and e.data1 == 123 for e in tail), "and All Notes Off sent"


async def test_seek_skips_earlier_events(rig):
    sink, player, events = rig
    await player.play("s1")
    await player.seek(0.1)
    await asyncio.sleep(0.4)

    played = [e.data1 for e in sink.sent if e.is_note_on]
    assert 60 not in played, "notes before the seek point should not sound"
    assert 62 in played


async def test_seek_while_stopped_stays_stopped(rig):
    sink, player, events = rig
    await player.play("s1")
    await player.stop()
    status = await player.seek(0.1)

    assert status["playing"] is False
    assert status["position"] == pytest.approx(0.1, abs=0.001)


async def test_looping_replays_from_the_start(rig):
    sink, player, events = rig
    await player.play("s1", speed=2.0, loop=True)
    await asyncio.sleep(0.7)

    assert player.playing, "a looping session keeps going"
    assert len([e for e in sink.sent if e.is_note_on and e.data1 == 60]) >= 2
    await player.stop()


async def test_speed_change_takes_effect_immediately(rig):
    sink, player, events = rig
    await player.play("s1", speed=0.5)
    status = await player.configure(speed=2.0)

    assert status["speed"] == 2.0
    assert status["playing"] is True


async def test_unknown_speed_snaps_to_the_nearest_supported_one(rig):
    sink, player, events = rig
    status = await player.play("s1", speed=3.7)
    assert status["speed"] == 2.0


async def test_playing_again_restarts_cleanly(rig):
    sink, player, events = rig
    await player.play("s1")
    await asyncio.sleep(0.05)
    await player.play("s1")

    assert player.playing
    assert player.position < 0.05, "a fresh play starts from the top"


async def test_refuses_to_play_with_no_instrument(settings):
    player = DevicePlayer(NullSink(settings), load_events=lambda _s: phrase())
    with pytest.raises(PlaybackUnavailable):
        await player.play("s1")


async def test_refuses_to_play_an_empty_recording(settings):
    sink = MockSink(settings)
    await sink.open()
    player = DevicePlayer(sink, load_events=lambda _s: [])
    with pytest.raises(PlaybackUnavailable):
        await player.play("s1")


async def test_status_is_broadcast_while_playing(rig):
    sink, player, events = rig
    seen: list[dict] = []
    player.on_change = seen.append

    await player.play("s1")
    await asyncio.sleep(0.35)
    await player.stop()

    assert len(seen) >= 2, "browsers need position updates during playback"
    assert seen[-1]["playing"] is False
