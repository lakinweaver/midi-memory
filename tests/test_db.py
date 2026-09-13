"""Search and filtering: the half of the app that makes recordings findable."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import Database, default_session_name
from app.midi.recorder import SessionRecord


@pytest.fixture
def db(settings) -> Database:
    return Database(settings.db_path)


def make_record(idx: int, *, days_ago: int = 0, duration_ms: int = 30_000,
                notes: int = 40, directory=None) -> SessionRecord:
    started = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return SessionRecord(
        id=f"session{idx:03d}",
        started_at=started,
        ended_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        event_count=notes * 2,
        note_count=notes,
        lowest_note=48, highest_note=84, avg_velocity=88.0,
        device_name="Test Piano",
        directory=directory or "/tmp",
    )


def test_insert_and_read_back(db):
    db.insert_session(make_record(1), name="Morning idea")
    session = db.get_session("session001")
    assert session["name"] == "Morning idea"
    assert session["note_count"] == 40
    assert session["tags"] == []
    assert session["favorite"] is False


def test_default_name_is_human_readable():
    name = default_session_name(datetime(2026, 9, 13, 14, 47, tzinfo=timezone.utc))
    assert "Sep 13" in name
    assert ("PM" in name or "AM" in name)


def test_rename_and_favourite(db):
    db.insert_session(make_record(1))
    assert db.update_session("session001", name="Waltz sketch", favorite=1)
    session = db.get_session("session001")
    assert session["name"] == "Waltz sketch"
    assert session["favorite"] is True


def test_tags_are_deduplicated_case_insensitively(db):
    db.insert_session(make_record(1))
    db.set_session_tags("session001", ["Jazz", "jazz", " JAZZ ", "blues"])
    assert db.get_session_tags("session001") == ["blues", "Jazz"]


def test_search_by_name_and_by_tag(db):
    db.insert_session(make_record(1), name="Waltz in C")
    db.insert_session(make_record(2), name="Random noodling")
    db.set_session_tags("session002", ["ambient"])

    assert [s["id"] for s in db.search(q="waltz")["items"]] == ["session001"]
    assert [s["id"] for s in db.search(q="ambient")["items"]] == ["session002"]


def test_multiple_tags_are_combined_with_and(db):
    db.insert_session(make_record(1), name="A")
    db.insert_session(make_record(2), name="B")
    db.set_session_tags("session001", ["jazz", "ballad"])
    db.set_session_tags("session002", ["jazz"])

    assert db.search(tags=["jazz"])["total"] == 2
    assert [s["id"] for s in db.search(tags=["jazz", "ballad"])["items"]] == ["session001"]


def test_filter_by_date_range_and_duration(db):
    db.insert_session(make_record(1, days_ago=0, duration_ms=10_000))
    db.insert_session(make_record(2, days_ago=10, duration_ms=120_000))

    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert [s["id"] for s in db.search(date_from=recent)["items"]] == ["session001"]
    assert [s["id"] for s in db.search(min_duration_ms=60_000)["items"]] == ["session002"]


def test_favorites_filter_and_sorting(db):
    db.insert_session(make_record(1, duration_ms=5_000), name="Zeta")
    db.insert_session(make_record(2, duration_ms=90_000), name="Alpha")
    db.update_session("session002", favorite=1)

    assert [s["id"] for s in db.search(favorite_only=True)["items"]] == ["session002"]
    assert [s["name"] for s in db.search(sort="name", order="asc")["items"]] == ["Alpha", "Zeta"]
    assert [s["id"] for s in db.search(sort="duration", order="desc")["items"]][0] == "session002"


def test_pagination_reports_total_and_has_more(db):
    for i in range(1, 8):
        db.insert_session(make_record(i))

    page = db.search(limit=3, offset=0)
    assert len(page["items"]) == 3
    assert page["total"] == 7
    assert page["has_more"] is True

    last = db.search(limit=3, offset=6)
    assert last["has_more"] is False


def test_tag_list_counts_and_pruning(db):
    db.insert_session(make_record(1))
    db.set_session_tags("session001", ["jazz"])
    assert db.list_tags() == [{"name": "jazz", "count": 1}]

    db.set_session_tags("session001", [])
    assert db.list_tags() == [], "tags with no sessions disappear from the filter bar"


def test_renaming_a_tag_merges_into_an_existing_one(db):
    db.insert_session(make_record(1))
    db.insert_session(make_record(2))
    db.set_session_tags("session001", ["jaz"])
    db.set_session_tags("session002", ["jazz"])

    assert db.rename_tag("jaz", "jazz")
    assert db.get_session_tags("session001") == ["jazz"]
    assert db.search(tags=["jazz"])["total"] == 2


def test_deleting_a_session_removes_its_files(db, settings, tmp_path):
    directory = settings.sessions_dir / "session001"
    directory.mkdir(parents=True)
    (directory / "session.mid").write_bytes(b"x")
    db.insert_session(make_record(1, directory=directory))

    assert db.delete_session("session001", settings.sessions_dir)
    assert not directory.exists()
    assert db.get_session("session001") is None


def test_date_filter_uses_local_days_not_utc_days(db):
    """A late-night session must show up under the day you actually played it."""
    from datetime import datetime

    # 11:30pm local time -- which is already "tomorrow" in UTC west of Greenwich.
    local_late = datetime(2026, 6, 15, 23, 30).astimezone()
    started_utc = local_late.astimezone(timezone.utc)

    record = make_record(1)
    record.started_at = started_utc
    record.ended_at = started_utc + timedelta(seconds=30)
    db.insert_session(record)

    on_the_night = db.search(date_from="2026-06-15", date_to="2026-06-15")
    assert on_the_night["total"] == 1, (
        f"session stored as {started_utc.isoformat()} should be found "
        "under its local date 2026-06-15"
    )

    next_day = db.search(date_from="2026-06-16", date_to="2026-06-16")
    assert next_day["total"] == 0


def test_date_filter_accepts_a_full_timestamp_too(db):
    db.insert_session(make_record(1, days_ago=0))
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert db.search(date_from=future)["total"] == 0
