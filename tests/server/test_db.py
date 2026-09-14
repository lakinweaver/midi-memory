"""Search and filtering: the half of the app that makes recordings findable."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from midi_memory.server.config import Settings
from midi_memory.server.db import Database, _day_bound, default_session_name
from midi_memory.shared.protocol import SessionUpload


@pytest.fixture
def db(settings) -> Database:
    return Database(settings.db_path)


def make_record(idx: int, *, days_ago: int = 0, duration_ms: int = 30_000,
                notes: int = 40, directory=None) -> SessionUpload:
    """A recording as it arrives from a client, which is all the server ever sees."""
    started = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return SessionUpload(
        id=f"session{idx:011d}",
        started_at=started,
        ended_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        event_count=notes * 2,
        note_count=notes,
        lowest_note=48, highest_note=84, avg_velocity=88.0,
        device_name="Test Piano",
    )


def test_insert_and_read_back(db):
    db.insert_session(make_record(1), name="Morning idea")
    session = db.get_session("session00000000001")
    assert session["name"] == "Morning idea"
    assert session["note_count"] == 40
    assert session["tags"] == []
    assert session["favorite"] is False


def test_default_name_is_human_readable():
    name = default_session_name(datetime(2026, 9, 13, 14, 47, tzinfo=timezone.utc),
                                timezone.utc)
    assert name == "Sep 13, 2:47 PM"


def test_default_name_uses_the_configured_zone():
    """The name is the one time the server formats itself, so it must be told
    what local means -- unset, a container is UTC and every name lands hours
    away from the timestamp the browser renders beside it."""
    started = datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc)
    assert default_session_name(started, ZoneInfo("America/New_York")) == "Sep 13, 9:30 PM"
    assert default_session_name(started, ZoneInfo("Europe/Berlin")) == "Sep 14, 3:30 AM"


def test_day_bound_reads_the_picker_in_the_configured_zone():
    """A bare YYYY-MM-DD out of <input type=date> is a calendar day in the
    library's zone, not the server process's."""
    zone = ZoneInfo("America/New_York")
    assert _day_bound("2026-09-13", end=False, zone=zone).startswith("2026-09-13T04:00")
    assert _day_bound("2026-09-13", end=True, zone=zone).startswith("2026-09-14T03:59")
    # Already a full timestamp: nothing to interpret, so nothing is done to it.
    assert _day_bound("2026-09-13T12:00:00+00:00", end=False, zone=zone) \
        == "2026-09-13T12:00:00+00:00"


def test_sessions_are_named_in_the_configured_zone(tmp_path):
    db = Database(tmp_path / "zoned.db", ZoneInfo("Europe/Berlin"))
    record = make_record(1)
    record.started_at = datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc)
    db.insert_session(record)
    assert db.get_session(record.id)["name"] == "Sep 14, 3:30 AM"


def test_unknown_zone_falls_back_rather_than_failing():
    """A typo should not stop the library serving; Settings shows what took
    effect instead."""
    s = Settings(_env_file=None, timezone="Mars/Olympus_Mons")
    assert s.zone is None
    assert "not found" in s.timezone_label


def test_zone_label_is_the_configured_name():
    s = Settings(_env_file=None, timezone="Europe/Berlin")
    assert s.zone == ZoneInfo("Europe/Berlin")
    assert s.timezone_label == "Europe/Berlin"


def test_rename_and_favourite(db):
    db.insert_session(make_record(1))
    assert db.update_session("session00000000001", name="Waltz sketch", favorite=1)
    session = db.get_session("session00000000001")
    assert session["name"] == "Waltz sketch"
    assert session["favorite"] is True


def test_tags_are_deduplicated_case_insensitively(db):
    db.insert_session(make_record(1))
    db.set_session_tags("session00000000001", ["Jazz", "jazz", " JAZZ ", "blues"])
    assert db.get_session_tags("session00000000001") == ["blues", "Jazz"]


def test_search_by_name_and_by_tag(db):
    db.insert_session(make_record(1), name="Waltz in C")
    db.insert_session(make_record(2), name="Random noodling")
    db.set_session_tags("session00000000002", ["ambient"])

    assert [s["id"] for s in db.search(q="waltz")["items"]] == ["session00000000001"]
    assert [s["id"] for s in db.search(q="ambient")["items"]] == ["session00000000002"]


def test_multiple_tags_are_combined_with_and(db):
    db.insert_session(make_record(1), name="A")
    db.insert_session(make_record(2), name="B")
    db.set_session_tags("session00000000001", ["jazz", "ballad"])
    db.set_session_tags("session00000000002", ["jazz"])

    assert db.search(tags=["jazz"])["total"] == 2
    assert [s["id"] for s in db.search(tags=["jazz", "ballad"])["items"]] == ["session00000000001"]


def test_filter_by_date_range_and_duration(db):
    db.insert_session(make_record(1, days_ago=0, duration_ms=10_000))
    db.insert_session(make_record(2, days_ago=10, duration_ms=120_000))

    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert [s["id"] for s in db.search(date_from=recent)["items"]] == ["session00000000001"]
    assert [s["id"] for s in db.search(min_duration_ms=60_000)["items"]] == ["session00000000002"]


def test_favorites_filter_and_sorting(db):
    db.insert_session(make_record(1, duration_ms=5_000), name="Zeta")
    db.insert_session(make_record(2, duration_ms=90_000), name="Alpha")
    db.update_session("session00000000002", favorite=1)

    assert [s["id"] for s in db.search(favorite_only=True)["items"]] == ["session00000000002"]
    assert [s["name"] for s in db.search(sort="name", order="asc")["items"]] == ["Alpha", "Zeta"]
    assert [s["id"] for s in db.search(sort="duration", order="desc")["items"]][0] == "session00000000002"


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
    db.set_session_tags("session00000000001", ["jazz"])
    assert db.list_tags() == [{"name": "jazz", "count": 1}]

    db.set_session_tags("session00000000001", [])
    assert db.list_tags() == [], "tags with no sessions disappear from the filter bar"


def test_renaming_a_tag_merges_into_an_existing_one(db):
    db.insert_session(make_record(1))
    db.insert_session(make_record(2))
    db.set_session_tags("session00000000001", ["jaz"])
    db.set_session_tags("session00000000002", ["jazz"])

    assert db.rename_tag("jaz", "jazz")
    assert db.get_session_tags("session00000000001") == ["jazz"]
    assert db.search(tags=["jazz"])["total"] == 2


def test_deleting_a_session_removes_its_files(db, settings, tmp_path):
    directory = settings.sessions_dir / "session00000000001"
    directory.mkdir(parents=True)
    (directory / "session.mid").write_bytes(b"x")
    db.insert_session(make_record(1, directory=directory))

    assert db.delete_session("session00000000001", settings.sessions_dir)
    assert not directory.exists()
    assert db.get_session("session00000000001") is None


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


def test_fingerprint_round_trips(db):
    record = make_record(1)
    record.fingerprint = [[0, 60, 12, 90], [40, 64, 10, 80]]
    db.insert_session(record)

    assert db.get_session("session00000000001")["fingerprint"] == record.fingerprint
    assert db.search()["items"][0]["fingerprint"] == record.fingerprint


def test_sessions_without_a_fingerprint_can_be_found_and_filled(db):
    """Recordings made before pitch strips existed get backfilled, not broken."""
    db.insert_session(make_record(1))
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET fingerprint = NULL")

    assert db.ids_missing_fingerprint() == ["session00000000001"]
    assert db.get_session("session00000000001")["fingerprint"] == [], "missing is empty, not an error"

    db.set_fingerprint("session00000000001", [[0, 60, 5, 100]])
    assert db.ids_missing_fingerprint() == []
    assert db.get_session("session00000000001")["fingerprint"] == [[0, 60, 5, 100]]


# -- adopting a library recorded before the server/client split ---------------
def test_a_pre_split_database_is_migrated_in_place(tmp_path):
    """The whole existing library has to survive the upgrade untouched.

    Builds the schema as it stood before clients existed, fills it, and then
    opens it with the current code -- which is exactly what happens the first
    time the server starts against a data directory it inherited.
    """
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '', event_count INTEGER NOT NULL DEFAULT 0,
                note_count INTEGER NOT NULL DEFAULT 0, lowest_note INTEGER,
                highest_note INTEGER, avg_velocity REAL,
                device_name TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO sessions VALUES
                ('older0000000001', '2024-01-01T10:00:00+00:00', '2024-01-01T10:00:30+00:00',
                 30000, 'An old idea', '', 80, 40, 48, 84, 88.0, 'Yamaha', 1,
                 '2024-01-01T10:00:00+00:00', '2024-01-01T10:00:00+00:00');
        """)

    db = Database(path)

    session = db.get_session("older0000000001")
    assert session["name"] == "An old idea"
    assert session["favorite"] is True, "stars survive"
    assert session["client_id"] is None, "nothing recorded it that we know of"
    assert session["client_name"] is None

    assert db.search()["total"] == 1
    # And the upgraded library still accepts new arrivals.
    db.insert_session(make_record(2), client_id=None)
    assert db.search()["total"] == 2
