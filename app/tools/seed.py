"""Generate demo recordings so the UI can be exercised without a piano.

    python -m app.tools.seed --count 14

Writes real session directories and real MIDI files through the same finalize
path the live recorder uses, so what you see is what you'd get.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import Database
from app.midi.events import MidiEvent
from app.midi.mock_source import SCALES
from app.midi.recorder import finalize_directory

NAMES = [
    "Morning thing", "That descending idea", "Slow 6/8", "Left-hand ostinato",
    "Chorus sketch", "Modal noodling", "Bridge attempt", "Late night",
    "Two-chord loop", "Something in D", "Waltz fragment", "Ballad opening",
    "Fast run practice", "Quiet ending", "Rhythm idea",
]
TAG_POOL = ["sketch", "keep", "jazz", "ambient", "ballad", "practice",
            "chords", "melody", "wip", "minor"]


def make_phrase(rng: random.Random) -> list[MidiEvent]:
    root = rng.choice([48, 53, 55, 57, 60, 62])
    scale = SCALES[rng.choice(list(SCALES))]
    tempo = rng.uniform(0.16, 0.44)
    length = rng.randint(14, 60)
    pedal = rng.random() < 0.55

    events: list[MidiEvent] = []
    t = 0.0
    pedal_down = False
    next_repedal = 0.0

    degree = rng.randrange(len(scale))
    for _ in range(length):
        # Re-pedal every bar or two, the way a player actually does, rather than
        # holding it for the whole phrase.
        if pedal and t >= next_repedal:
            if pedal_down:
                events.append(MidiEvent(t, 0xB0, 64, 0))
            events.append(MidiEvent(t + 0.01, 0xB0, 64, 127))
            pedal_down = True
            next_repedal = t + rng.uniform(1.2, 3.0)

        degree += rng.choice([-2, -1, -1, 1, 1, 2, 3])
        degree = max(0, min(len(scale) * 2 - 1, degree))
        octave, index = divmod(degree, len(scale))
        note = max(21, min(108, root + scale[index] + 12 * octave))
        velocity = max(28, min(122, int(rng.gauss(76, 16))))

        chord = [note]
        if rng.random() < 0.3:
            chord.append(max(21, min(108, note + 7)))
            if rng.random() < 0.5:
                chord.append(max(21, min(108, note + 12)))

        hold = tempo * rng.uniform(0.6, 1.5)
        for pitch in chord:
            events.append(MidiEvent(t, 0x90, pitch, velocity))
        for pitch in chord:
            events.append(MidiEvent(t + hold, 0x80, pitch, 0))
        t += hold + (tempo * rng.uniform(0.0, 0.6) if rng.random() < 0.25 else 0)

    if pedal_down:
        events.append(MidiEvent(t + 0.5, 0xB0, 64, 0))
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=14)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    settings = get_settings()
    settings.ensure_dirs()
    db = Database(settings.db_path)

    created = 0
    for i in range(args.count):
        session_id = f"demo{i:03d}{rng.randrange(16**6):06x}"
        directory = settings.sessions_dir / session_id
        directory.mkdir(parents=True, exist_ok=True)

        events = make_phrase(rng)
        started = datetime.now(timezone.utc) - timedelta(
            days=rng.randrange(0, 26),
            hours=rng.randrange(0, 24),
            minutes=rng.randrange(0, 60),
        )
        record = finalize_directory(
            directory, settings, events=events, started_at=started,
            device_name="Yamaha P-125 (demo)",
        )
        if record is None:
            continue

        db.insert_session(record, name=rng.choice(NAMES))
        if rng.random() < 0.75:
            db.set_session_tags(session_id, rng.sample(TAG_POOL, rng.randint(1, 3)))
        if rng.random() < 0.25:
            db.update_session(session_id, favorite=1)
        created += 1

    print(f"Created {created} demo session(s) in {settings.sessions_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
