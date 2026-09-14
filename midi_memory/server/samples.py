"""The piano sample set used by the browser player.

Shared by the setup script and the web UI, so there is one list of files and one
download routine rather than two that can drift apart.

Samples: Salamander Grand Piano by Alexander Holm, CC BY 3.0, as redistributed
by the Tone.js project.
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://tonejs.github.io/audio/salamander/"
MANIFEST_NAME = "manifest.json"
TIMEOUT_SECONDS = 30
ATTEMPTS = 3

# One sample every minor third across the keyboard: the browser pitch-shifts at
# most one semitone either way, which is inaudible on piano.
SAMPLES: list[tuple[str, int]] = [
    ("A0", 21), ("C1", 24), ("Ds1", 27), ("Fs1", 30),
    ("A1", 33), ("C2", 36), ("Ds2", 39), ("Fs2", 42),
    ("A2", 45), ("C3", 48), ("Ds3", 51), ("Fs3", 54),
    ("A3", 57), ("C4", 60), ("Ds4", 63), ("Fs4", 66),
    ("A4", 69), ("C5", 72), ("Ds5", 75), ("Fs5", 78),
    ("A5", 81), ("C6", 84), ("Ds6", 87), ("Fs6", 90),
    ("A6", 93), ("C7", 96), ("Ds7", 99), ("Fs7", 102),
    ("A7", 105), ("C8", 108),
]


def audio_dir() -> Path:
    return Path(__file__).resolve().parent / "static" / "audio"


def _present(directory: Path) -> list[tuple[str, int]]:
    """Samples already on disk and big enough to be real."""
    found = []
    for name, midi in SAMPLES:
        path = directory / f"{name}.mp3"
        if path.exists() and path.stat().st_size > 1000:
            found.append((name, midi))
    return found


def status(directory: Path | None = None) -> dict:
    directory = directory or audio_dir()
    present = _present(directory)
    manifest = directory / MANIFEST_NAME
    return {
        "installed": len(present),
        "expected": len(SAMPLES),
        # The player reads the manifest first, so samples without it are unusable.
        "ready": bool(present) and manifest.exists(),
        "manifest_present": manifest.exists(),
    }


def write_manifest(directory: Path) -> int:
    """Point the manifest at whatever is actually on disk."""
    present = _present(directory)
    (directory / MANIFEST_NAME).write_text(
        json.dumps(
            {"format": "mp3", "samples": {str(midi): f"{name}.mp3" for name, midi in present}},
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(present)


def ensure_manifest(directory: Path | None = None) -> bool:
    """Rebuild the manifest if samples are present but it is not.

    The manifest is generated, not source, so it is not in the repository -- and
    it can go missing independently of the audio: restored from a backup, copied
    across by hand, or removed by a pull. Since it can always be derived from
    what is on disk, derive it rather than making someone notice that playback
    quietly got worse.
    """
    directory = directory or audio_dir()
    if not directory.exists():
        return False
    present = _present(directory)
    if not present or (directory / MANIFEST_NAME).exists():
        return False
    write_manifest(directory)
    log.info("Rebuilt the sample manifest from %d file(s) on disk", len(present))
    return True


def _download_one(url: str, target: Path) -> bool:
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
                log.warning("Could not download %s: %s", target.name, exc)
                return False
            time.sleep(attempt)
    return False


def download(directory: Path | None = None, force: bool = False,
             on_progress=None) -> dict:
    """Fetch any missing samples. Blocking; call it off the event loop.

    Re-running is cheap: files already present are skipped, so an interrupted
    download resumes rather than starting over.
    """
    directory = directory or audio_dir()
    directory.mkdir(parents=True, exist_ok=True)

    downloaded, skipped, failed = 0, 0, []
    for index, (name, _midi) in enumerate(SAMPLES, start=1):
        target = directory / f"{name}.mp3"
        if not force and target.exists() and target.stat().st_size > 1000:
            skipped += 1
        elif _download_one(BASE_URL + f"{name}.mp3", target):
            downloaded += 1
        else:
            failed.append(f"{name}.mp3")
        if on_progress is not None:
            on_progress(index, len(SAMPLES), name)

    in_manifest = write_manifest(directory)
    result = {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        **status(directory),
    }
    log.info("Sample download finished: %d new, %d already present, %d failed",
             downloaded, skipped, len(failed))
    if not in_manifest:
        log.warning("No samples available; the player will use its built-in tone")
    return result
