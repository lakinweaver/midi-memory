#!/usr/bin/env python3
"""Download the piano samples the browser player uses, into app/static/audio/.

Run once during setup. The app works without them -- it falls back to a built-in
synthesised tone -- but sampled piano is far nicer for judging whether an idea
was any good.

Samples: Salamander Grand Piano by Alexander Holm, CC BY 3.0, as redistributed
by the Tone.js project. Attribution is reproduced in README.md.
"""
from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_SECONDS = 30
ATTEMPTS = 3

BASE_URL = "https://tonejs.github.io/audio/salamander/"
OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "audio"

# One sample every minor third across the keyboard: the browser pitch-shifts at
# most one semitone either way, which is inaudible on piano.
SAMPLES = [
    ("A0", 21), ("C1", 24), ("Ds1", 27), ("Fs1", 30),
    ("A1", 33), ("C2", 36), ("Ds2", 39), ("Fs2", 42),
    ("A2", 45), ("C3", 48), ("Ds3", 51), ("Fs3", 54),
    ("A3", 57), ("C4", 60), ("Ds4", 63), ("Fs4", 66),
    ("A4", 69), ("C5", 72), ("Ds5", 75), ("Fs5", 78),
    ("A5", 81), ("C6", 84), ("Ds6", 87), ("Fs6", 90),
    ("A6", 93), ("C7", 96), ("Ds7", 99), ("Fs7", 102),
    ("A7", 105), ("C8", 108),
]


def _download(url: str, target: Path) -> bool:
    """Fetch one sample, retrying briefly.

    A Pi on wifi drops connections far more readily than a laptop, and losing the
    whole sample set to one flaky socket is not worth it.
    """
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
                data = response.read()
            target.write_bytes(data)
            return True
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            if attempt == ATTEMPTS:
                print(f"  FAILED {target.name} after {ATTEMPTS} attempts: {exc}")
                return False
            time.sleep(attempt)  # brief backoff before trying again
    return False


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    total = 0
    failed: list[str] = []

    for name, midi in SAMPLES:
        filename = f"{name}.mp3"
        target = OUT_DIR / filename
        if target.exists() and target.stat().st_size > 1000:
            manifest[str(midi)] = filename
            total += target.stat().st_size
            print(f"  have {filename}")
            continue
        if not _download(BASE_URL + filename, target):
            failed.append(filename)
            continue
        size = target.stat().st_size
        total += size
        manifest[str(midi)] = filename
        print(f"  got  {filename}  ({size / 1024:.0f} KB)")

    if not manifest:
        print("\nNo samples downloaded. The player will use its built-in synth instead.")
        return 1

    (OUT_DIR / "manifest.json").write_text(
        json.dumps({"format": "mp3", "samples": manifest}, indent=2), encoding="utf-8"
    )
    print(f"\n{len(manifest)} samples, {total / 1_048_576:.1f} MB -> {OUT_DIR}")
    if failed:
        print(f"{len(failed)} failed; the player will pitch-shift further to cover "
              "the gaps. Re-run this script to pick them up -- files already "
              "downloaded are skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
