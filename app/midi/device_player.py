"""Plays a recorded session back out to the attached instrument.

Unlike browser playback, this is a single shared resource: there is one piano, so
there is one transport, and every connected browser sees the same state.

It replays the raw recorded events rather than extracted notes, so the pedal,
pitch bend and velocity come back exactly as they were played.
"""
from __future__ import annotations

import asyncio
import logging
import time
from bisect import bisect_left
from typing import Callable, Optional

from app.midi.events import MidiEvent
from app.midi.sink import MidiSink

log = logging.getLogger(__name__)

BROADCAST_INTERVAL = 0.25   # how often the browsers are told where the playhead is
MIN_SLEEP = 0.0005
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class DevicePlayer:
    def __init__(
        self,
        sink: MidiSink,
        load_events: Callable[[str], list[MidiEvent]],
        on_change: Optional[Callable[[dict], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sink = sink
        self.load_events = load_events
        self.on_change = on_change
        self.clock = clock

        self.session_id: Optional[str] = None
        self.speed: float = 1.0
        self.loop: bool = False
        self.duration: float = 0.0

        self._events: list[MidiEvent] = []
        self._times: list[float] = []
        self._origin: float = 0.0      # playhead position when the run started
        self._anchor: float = 0.0      # clock() at that moment
        self._paused_at: float = 0.0
        self._task: Optional[asyncio.Task] = None
        self._ticker: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # -- state ---------------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def position(self) -> float:
        if not self.playing:
            return self._paused_at
        elapsed = (self.clock() - self._anchor) * self.speed
        return min(self.duration, max(0.0, self._origin + elapsed))

    def status(self) -> dict:
        return {
            "playing": self.playing,
            "session_id": self.session_id,
            "position": round(self.position, 3),
            "duration": round(self.duration, 3),
            "speed": self.speed,
            "loop": self.loop,
            "output": self.sink.status(),
        }

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self.status())
            except Exception:
                log.exception("Playback status broadcast failed")

    # -- transport -----------------------------------------------------------
    async def play(self, session_id: str, position: float = 0.0,
                   speed: float = 1.0, loop: bool = False) -> dict:
        async with self._lock:
            if not self.sink.connected:
                raise PlaybackUnavailable("No MIDI instrument is connected")

            await self._halt()

            if session_id != self.session_id or not self._events:
                events = self.load_events(session_id)
                if not events:
                    raise PlaybackUnavailable("That recording has no playable events")
                self._events = events
                self._times = [e.t for e in events]
                self.duration = events[-1].t
                self.session_id = session_id

            self.speed = _clamp_speed(speed)
            self.loop = bool(loop)
            self._paused_at = min(max(0.0, position), self.duration)
            self._start()
            self._notify()
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            await self._halt()
            self._notify()
            return self.status()

    async def seek(self, position: float) -> dict:
        async with self._lock:
            was_playing = self.playing
            await self._halt()
            self._paused_at = min(max(0.0, position), self.duration)
            if was_playing:
                self._start()
            self._notify()
            return self.status()

    async def configure(self, speed: Optional[float] = None,
                        loop: Optional[bool] = None) -> dict:
        async with self._lock:
            if loop is not None:
                self.loop = bool(loop)
            if speed is not None and _clamp_speed(speed) != self.speed:
                # Re-anchor at the current playhead so the change takes effect now.
                was_playing = self.playing
                await self._halt()
                self.speed = _clamp_speed(speed)
                if was_playing:
                    self._start()
            self._notify()
            return self.status()

    # -- engine --------------------------------------------------------------
    def _start(self) -> None:
        self._origin = self._paused_at
        self._anchor = self.clock()
        self._task = asyncio.create_task(self._run(), name="device-playback")
        self._ticker = asyncio.create_task(self._broadcast(), name="device-playback-tick")

    async def _halt(self) -> None:
        """Stop the engine and leave the instrument silent."""
        position = self.position
        for task in (self._task, self._ticker):
            if task is not None:
                task.cancel()
        for task in (self._task, self._ticker):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = self._ticker = None
        self._paused_at = position
        # Whatever was ringing when we cut the stream has to be damped, or the
        # piano sustains that chord until someone power-cycles it.
        await self.sink.panic()

    async def _run(self) -> None:
        try:
            while True:
                index = bisect_left(self._times, self._origin)
                while index < len(self._events):
                    event = self._events[index]
                    delay = (self._anchor + (event.t - self._origin) / self.speed) - self.clock()
                    if delay > MIN_SLEEP:
                        await asyncio.sleep(delay)
                    await self.sink.send(event)
                    index += 1

                if not self.loop:
                    break
                await self.sink.panic()
                self._origin = 0.0
                self._anchor = self.clock()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Device playback failed")
        finally:
            if not self.loop:
                asyncio.create_task(self._finished())

    async def _finished(self) -> None:
        """Natural end of the recording: tidy up and tell the browsers."""
        async with self._lock:
            if self._ticker is not None:
                self._ticker.cancel()
                self._ticker = None
            self._task = None
            self._paused_at = self.duration
            await self.sink.panic()
            self._notify()

    async def _broadcast(self) -> None:
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL)
            self._notify()


class PlaybackUnavailable(RuntimeError):
    """Raised when playback cannot start: no instrument, or nothing to play."""


def _clamp_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return 1.0
    return min(SPEEDS, key=lambda s: abs(s - value))
