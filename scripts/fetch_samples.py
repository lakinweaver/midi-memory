#!/usr/bin/env python3
"""Download the piano samples the server's browser player uses.

    python scripts/fetch_samples.py

Only the server needs these -- a capture client has no player. Safe to re-run:
files already present are skipped, so an interrupted download resumes.
The Settings dialog has a button that does exactly the same thing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from midi_memory.server import samples  # noqa: E402


def main() -> int:
    def progress(index: int, total: int, name: str) -> None:
        print(f"  [{index:2d}/{total}] {name}", flush=True)

    print(f"Fetching {len(samples.SAMPLES)} piano samples into {samples.audio_dir()}")
    result = samples.download(on_progress=progress)
    print(f"\n{result['downloaded']} downloaded, {result['skipped']} already present, "
          f"{len(result['failed'])} failed")
    if result["failed"]:
        print("Failed: " + ", ".join(result["failed"]))
        print("Re-run to retry just those.")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
