"""Making a write survive the wall socket.

A capture client runs on a Pi with no battery: it is switched off by the plug
being pulled, usually mid-take. `write()` and even `flush()` only hand bytes to
the kernel's page cache, which Linux is free to sit on for tens of seconds, so
code that looks like it has saved something can still lose it. The two tools
here are what actually make a write durable, and the recorder and the spool use
them at the points where losing a take would be unrecoverable.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator


def fsync_dir(directory: Path) -> None:
    """Persist a directory's own entries: creations, renames, deletions.

    Syncing a file says nothing about whether its *name* survives -- that lives
    in the parent directory, which has to be synced separately. Skipped quietly
    where the platform will not open a directory for reading (Windows), since
    the alternative is refusing to run on a developer's machine to honour a
    guarantee only the deployment actually needs.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def atomic_write(path: Path, mode: str = "wb",
                 encoding: str | None = None) -> Iterator[IO]:
    """Write through a temporary file that is synced, then renamed into place.

    A reader therefore sees either the previous contents or the complete new
    ones and never a half-written file, which matters most where the mere
    existence of a file is a signal -- `session.mid` marking a take as rendered,
    `upload.json` marking one as ready to send.
    """
    tmp = path.with_name(path.name + ".tmp")
    handle = tmp.open(mode, encoding=encoding)
    try:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
    except BaseException:
        handle.close()
        tmp.unlink(missing_ok=True)
        raise
    handle.close()
    tmp.replace(path)
    # The rename is itself only a change to the directory, and just as losable.
    fsync_dir(path.parent)
