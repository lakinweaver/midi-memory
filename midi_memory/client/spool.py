"""The local queue of recordings waiting to reach the server.

Sessions are recorded straight into the spool, so a take is never copied or moved
while it is being played -- the recorder appends to `spool/<id>/events.jsonl` and
the finaliser renders `session.mid` beside it. A directory becomes *pending* only
once `upload.json` lands, which is the last write: anything without it was either
still being played or interrupted, and is picked up by crash recovery instead.

Nothing here talks to the network. The uploader drains what this exposes, so a
server that is down or unreachable costs nothing but disk.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Iterator, Optional

from midi_memory.client.config import Settings
from midi_memory.client.midi.recorder import (
    EVENTS_FILENAME,
    MIDI_FILENAME,
    SessionRecord,
    UPLOAD_FILENAME,
)
from midi_memory.shared.durable import atomic_write
from midi_memory.shared.protocol import SessionUpload

log = logging.getLogger(__name__)


class SpooledSession:
    """One finished recording sitting on disk, ready to upload."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.id = directory.name

    @property
    def midi_path(self) -> Path:
        return self.directory / MIDI_FILENAME

    @property
    def events_path(self) -> Path:
        return self.directory / EVENTS_FILENAME

    def payload(self) -> SessionUpload:
        raw = (self.directory / UPLOAD_FILENAME).read_text(encoding="utf-8")
        return SessionUpload.model_validate_json(raw)

    def __repr__(self) -> str:
        return f"<SpooledSession {self.id}>"


class Spool:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- writing -------------------------------------------------------------
    def add(self, record: SessionRecord) -> SpooledSession:
        """Mark a finalised recording as ready to upload.

        Written to a temporary name, synced, and renamed, so a crash mid-write
        cannot leave a half-written payload that the uploader would then reject
        forever. The sync is not redundant with the rename: a rename can reach
        the card while the contents it points at are still in the page cache,
        which is how you end up with a file full of zeros.
        """
        payload = SessionUpload.from_record(record)
        directory = record.directory
        with atomic_write(directory / UPLOAD_FILENAME, "w", encoding="utf-8") as handle:
            handle.write(payload.model_dump_json(indent=2))
        log.info("Spooled session %s (%d notes) for upload", record.id, record.note_count)
        return SpooledSession(directory)

    # -- reading -------------------------------------------------------------
    def pending(self) -> list[SpooledSession]:
        """Everything waiting to upload, oldest first.

        Oldest first matters: if the server has been unreachable all evening, the
        takes should arrive in the order they were played. Session ids are random,
        so ordering comes from when `upload.json` was written -- the last write a
        session gets. Only one take is ever recorded at a time, so finish order is
        play order, and crash recovery spools its backlog in start-time order to
        keep that true.
        """
        return sorted(self._scan(), key=_spooled_at)

    def _scan(self) -> Iterator[SpooledSession]:
        spool = self.settings.spool_dir
        if not spool.exists():
            return
        for directory in spool.iterdir():
            if not directory.is_dir():
                continue
            if (directory / UPLOAD_FILENAME).exists() and (directory / MIDI_FILENAME).exists():
                yield SpooledSession(directory)

    def pending_count(self) -> int:
        return sum(1 for _ in self._scan())

    def get(self, session_id: str) -> Optional[SpooledSession]:
        directory = self.settings.spool_dir / session_id
        if (directory / UPLOAD_FILENAME).exists():
            return SpooledSession(directory)
        return None

    # -- retiring ------------------------------------------------------------
    def complete(self, session_id: str) -> None:
        """The server has it. Keep a local copy for a while, or drop it."""
        directory = self.settings.spool_dir / session_id
        if not directory.is_dir():
            return
        if self.settings.keep_uploaded_days <= 0:
            shutil.rmtree(directory, ignore_errors=True)
            return
        self.settings.uploaded_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.uploaded_dir / session_id
        shutil.rmtree(target, ignore_errors=True)
        try:
            directory.replace(target)
        except OSError:
            log.exception("Could not archive %s after upload", session_id)

    def prune(self) -> int:
        """Delete archived copies older than the retention window."""
        days = self.settings.keep_uploaded_days
        archive = self.settings.uploaded_dir
        if days <= 0 or not archive.exists():
            return 0
        cutoff = time.time() - days * 86_400
        removed = 0
        for directory in archive.iterdir():
            if not directory.is_dir():
                continue
            try:
                if directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        if removed:
            log.info("Pruned %d uploaded session(s) past the retention window", removed)
        return removed

    # -- reporting -----------------------------------------------------------
    def summary(self) -> dict:
        pending = self.pending()
        return {
            "pending": len(pending),
            "oldest_pending": pending[0].id if pending else None,
        }


def _spooled_at(session: SpooledSession) -> float:
    try:
        return (session.directory / UPLOAD_FILENAME).stat().st_mtime
    except OSError:
        return 0.0
