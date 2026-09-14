"""A synthetic keyboard, for developing on a machine with no piano attached.

It plays short phrases separated by silences longer than the idle threshold, so the
real segmentation path gets exercised end to end rather than stubbed out.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path

from midi_memory.shared.midi.events import MidiEvent
from midi_memory.shared.midi.smf import read_smf
from midi_memory.client.midi.source import MidiSource

log = logging.getLogger(__name__)

# Scale degrees (semitones from the root) for a handful of usable colours.
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "pentatonic": [0, 2, 4, 7, 9],
    "blues": [0, 3, 5, 6, 7, 10],
}


class MockSource(MidiSource):
    kind = "mock"

    def __init__(self, settings, replay_file: Path | None = None) -> None:
        super().__init__(settings)
        self.replay_file = replay_file
        self.rng = random.Random()

    async def run(self) -> None:
        self.set_status(True, "Mock Keyboard")
        try:
            while not self._closing:
                if self.replay_file is not None:
                    await self._replay(self.replay_file)
                else:
                    await self._improvise()
                # Go quiet for longer than the idle threshold so the recorder
                # closes the session, exactly as it would between real ideas.
                await asyncio.sleep(self.settings.idle_seconds + 3)
        finally:
            self.set_status(False, "")

    # -- generators ----------------------------------------------------------
    async def _improvise(self) -> None:
        rng = self.rng
        root = rng.choice([48, 53, 55, 57, 60, 62])
        scale = SCALES[rng.choice(list(SCALES))]
        tempo = rng.uniform(0.18, 0.42)  # seconds per note
        phrase_len = rng.randint(8, 22)
        use_pedal = rng.random() < 0.5

        if use_pedal:
            self.emit(MidiEvent(time.monotonic(), 0xB0, 64, 127))

        degree = rng.randrange(len(scale))
        for i in range(phrase_len):
            if self._closing:
                return
            # Mostly stepwise motion, with the occasional leap.
            degree += rng.choice([-2, -1, -1, 1, 1, 2, 3]) if rng.random() < 0.85 else rng.randint(-4, 4)
            degree = max(0, min(len(scale) * 2 - 1, degree))
            octave, index = divmod(degree, len(scale))
            note = root + scale[index] + 12 * octave
            velocity = max(30, min(120, int(rng.gauss(78, 14))))

            chord = [note]
            if rng.random() < 0.28:  # a chord every so often
                chord += [note + scale[(index + 2) % len(scale)] % 12 + 4,
                          note + 7]

            for pitch in chord:
                self.emit(MidiEvent(time.monotonic(), 0x90, max(21, min(108, pitch)), velocity))
            hold = tempo * rng.uniform(0.6, 1.4)
            await asyncio.sleep(hold)
            for pitch in chord:
                self.emit(MidiEvent(time.monotonic(), 0x80, max(21, min(108, pitch)), 0))
            if rng.random() < 0.15:
                await asyncio.sleep(tempo * rng.uniform(0.5, 1.5))

        if use_pedal:
            await asyncio.sleep(0.6)
            self.emit(MidiEvent(time.monotonic(), 0xB0, 64, 0))

    async def _replay(self, path: Path) -> None:
        """Play a real MIDI file through in real time, as if performed live."""
        events = read_smf(path)
        if not events:
            log.warning("Replay file %s produced no events", path)
            return
        start = time.monotonic()
        origin = events[0].t
        for event in events:
            if self._closing:
                return
            target = start + (event.t - origin)
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.emit(MidiEvent(time.monotonic(), event.status, event.data1, event.data2))
