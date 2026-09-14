"""The local queue: what counts as pending, in what order, and what is kept."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from midi_memory.client.midi.recorder import SessionRecord
from midi_memory.client.spool import Spool
from midi_memory.shared.protocol import MIDI_FILENAME


def make_record(settings, session_id: str, notes: int = 6) -> SessionRecord:
    """A finalised session on disk, as the recorder would have left it."""
    directory = settings.spool_dir / session_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MIDI_FILENAME).write_bytes(b"MThd fake")
    started = datetime.now(timezone.utc)
    return SessionRecord(
        id=session_id, started_at=started,
        ended_at=started + timedelta(seconds=4), duration_ms=4000,
        event_count=notes * 2, note_count=notes, lowest_note=60, highest_note=72,
        avg_velocity=80.0, device_name="Yamaha", directory=directory,
        fingerprint=[[0, 60, 8, 80]],
    )


def test_a_session_is_not_pending_until_it_has_an_upload_payload(settings, spool):
    """A directory being recorded into must not be picked up mid-take."""
    make_record(settings, "aaaa1111")
    assert spool.pending() == []

    spool.add(make_record(settings, "aaaa1111"))
    assert [s.id for s in spool.pending()] == ["aaaa1111"]


def test_the_payload_round_trips_through_disk(settings, spool):
    spool.add(make_record(settings, "aaaa1111", notes=9))
    payload = spool.pending()[0].payload()

    assert payload.id == "aaaa1111"
    assert payload.note_count == 9
    assert payload.device_name == "Yamaha"
    assert payload.fingerprint == [[0, 60, 8, 80]]


def test_pending_is_ordered_by_when_each_take_finished(settings, spool):
    for session_id in ("zzzz9999", "aaaa1111", "mmmm5555"):
        spool.add(make_record(settings, session_id))
        time.sleep(0.01)   # distinct mtimes; ids sort the other way on purpose

    assert [s.id for s in spool.pending()] == ["zzzz9999", "aaaa1111", "mmmm5555"]


def test_completing_archives_the_take_when_copies_are_kept(settings, spool):
    settings.keep_uploaded_days = 7
    spool.add(make_record(settings, "aaaa1111"))
    spool.complete("aaaa1111")

    assert spool.pending() == []
    assert (settings.uploaded_dir / "aaaa1111" / MIDI_FILENAME).exists(), (
        "the take is kept locally as insurance, not thrown away"
    )


def test_completing_deletes_the_take_when_no_copies_are_kept(settings, spool):
    settings.keep_uploaded_days = 0
    spool.add(make_record(settings, "aaaa1111"))
    spool.complete("aaaa1111")

    assert spool.pending() == []
    assert not (settings.spool_dir / "aaaa1111").exists()
    assert not (settings.uploaded_dir / "aaaa1111").exists()


def test_pruning_drops_archived_takes_past_the_window(settings, spool):
    settings.keep_uploaded_days = 7
    spool.add(make_record(settings, "old00001"))
    spool.add(make_record(settings, "new00001"))
    spool.complete("old00001")
    spool.complete("new00001")

    stale = time.time() - 8 * 86_400
    import os
    os.utime(settings.uploaded_dir / "old00001", (stale, stale))

    assert spool.prune() == 1
    assert not (settings.uploaded_dir / "old00001").exists()
    assert (settings.uploaded_dir / "new00001").exists()


def test_completing_something_that_is_not_there_is_harmless(spool):
    spool.complete("never-existed")   # a retry after the archive already happened
    assert spool.pending() == []
