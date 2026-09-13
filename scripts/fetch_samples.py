#!/usr/bin/env python3
"""Download the piano samples the browser player uses.

    python scripts/fetch_samples.py

The app works without them -- it falls back to a synthesised tone -- but sampled
piano is far nicer for judging whether an idea was any good. They can also be
downloaded from the web UI, under the cogwheel.

Attribution for the sample set is in README.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.samples import audio_dir, download  # noqa: E402


def main() -> int:
    def report(index: int, total: int, name: str) -> None:
        print(f"  [{index:2d}/{total}] {name}.mp3")

    result = download(on_progress=report)
    print(f"\n{result['installed']}/{result['expected']} samples in {audio_dir()}")
    if result["failed"]:
        print(f"{len(result['failed'])} failed; re-run to retry just those.")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
