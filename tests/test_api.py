"""HTTP surface: search, editing, tags, download, and the login gate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.midi.events import MidiEvent
from app.midi.recorder import SessionRecord
from app.midi.smf import write_smf


def seed(client: TestClient, session_id: str, name: str, *, days_ago: int = 0,
         notes: int = 6, favorite: bool = False) -> None:
    """Create a real session on disk and in the database."""
    app = client.app
    settings = app.state.settings
    directory = settings.sessions_dir / session_id
    directory.mkdir(parents=True, exist_ok=True)

    events = []
    for i in range(notes):
        events.append(MidiEvent(i * 0.5, 0x90, 60 + i, 80 + i))
        events.append(MidiEvent(i * 0.5 + 0.4, 0x80, 60 + i, 0))
    write_smf(events, directory / "session.mid", name=name)

    started = datetime.now(timezone.utc) - timedelta(days=days_ago)
    app.state.db.insert_session(
        SessionRecord(
            id=session_id, started_at=started,
            ended_at=started + timedelta(seconds=notes * 0.5),
            duration_ms=int(notes * 500), event_count=notes * 2, note_count=notes,
            lowest_note=60, highest_note=60 + notes - 1, avg_velocity=83.0,
            device_name="Test Piano", directory=directory,
        ),
        name=name,
    )
    if favorite:
        app.state.db.update_session(session_id, favorite=1)


@pytest.fixture
def client(settings):
    settings.midi_source = "none"
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def auth_client(settings):
    settings.midi_source = "none"
    settings.password = "hunter2"
    with TestClient(create_app(settings)) as c:
        yield c


# -- basics ------------------------------------------------------------------
def test_health_and_status(client):
    assert client.get("/healthz").json()["ok"] is True

    status = client.get("/api/status").json()
    assert status["source"] == "none"
    assert status["recording"] is False
    assert "stats" in status


def test_library_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "MIDI" in response.text


# -- search ------------------------------------------------------------------
def test_search_filters_and_sorts(client):
    seed(client, "aaa", "Waltz in C", days_ago=0, notes=6)
    seed(client, "bbb", "Late night blues", days_ago=9, notes=12, favorite=True)

    assert client.get("/api/sessions").json()["total"] == 2
    assert [s["id"] for s in client.get("/api/sessions?q=waltz").json()["items"]] == ["aaa"]
    assert [s["id"] for s in client.get("/api/sessions?favorite=true").json()["items"]] == ["bbb"]

    longest = client.get("/api/sessions?sort=duration&order=desc").json()["items"]
    assert longest[0]["id"] == "bbb"


def test_search_by_tag(client):
    seed(client, "aaa", "One")
    seed(client, "bbb", "Two")
    client.put("/api/sessions/aaa/tags", json={"tags": ["ballad", "jazz"]})
    client.put("/api/sessions/bbb/tags", json={"tags": ["jazz"]})

    assert client.get("/api/sessions?tag=jazz").json()["total"] == 2
    both = client.get("/api/sessions?tag=jazz&tag=ballad").json()
    assert [s["id"] for s in both["items"]] == ["aaa"]


def test_pagination(client):
    for i in range(5):
        seed(client, f"s{i}", f"Take {i}")
    page = client.get("/api/sessions?limit=2").json()
    assert len(page["items"]) == 2 and page["total"] == 5 and page["has_more"] is True


# -- editing -----------------------------------------------------------------
def test_rename_star_and_annotate(client):
    seed(client, "aaa", "Untitled")

    renamed = client.patch("/api/sessions/aaa", json={"name": "  Nocturne  "}).json()
    assert renamed["name"] == "Nocturne"

    starred = client.patch("/api/sessions/aaa", json={"favorite": True}).json()
    assert starred["favorite"] is True

    noted = client.patch("/api/sessions/aaa", json={"notes": "revisit the bridge"}).json()
    assert noted["notes"] == "revisit the bridge"


def test_blank_name_falls_back_rather_than_saving_empty(client):
    seed(client, "aaa", "Something")
    assert client.patch("/api/sessions/aaa", json={"name": "   "}).json()["name"] == "Untitled"


def test_add_and_remove_tags(client):
    seed(client, "aaa", "One")

    added = client.post("/api/sessions/aaa/tags", json={"tag": "sketch"}).json()
    assert added["tags"] == ["sketch"]

    client.post("/api/sessions/aaa/tags", json={"tag": "minor"})
    removed = client.delete("/api/sessions/aaa/tags/sketch").json()
    assert removed["tags"] == ["minor"]

    assert [t["name"] for t in client.get("/api/tags").json()["tags"]] == ["minor"]


def test_rename_tag_across_sessions(client):
    seed(client, "aaa", "One"); seed(client, "bbb", "Two")
    client.put("/api/sessions/aaa/tags", json={"tags": ["wip"]})
    client.put("/api/sessions/bbb/tags", json={"tags": ["wip"]})

    client.patch("/api/tags/wip", json={"name": "in progress"})
    assert client.get("/api/sessions/aaa").json()["tags"] == ["in progress"]
    assert client.get("/api/sessions?tag=in progress").json()["total"] == 2


# -- playback + download -----------------------------------------------------
def test_notes_endpoint_feeds_the_player(client):
    seed(client, "aaa", "One", notes=6)
    payload = client.get("/api/sessions/aaa/notes").json()

    assert len(payload["notes"]) == 6
    first = payload["notes"][0]
    assert {"n", "s", "d", "v"} <= set(first)
    assert first["d"] > 0, "notes need a duration or nothing will sound"


def test_download_serves_a_real_midi_file(client):
    seed(client, "aaa", "Waltz in C")
    response = client.get("/api/sessions/aaa/download")

    assert response.status_code == 200
    assert response.content[:4] == b"MThd", "must be a real Standard MIDI File"
    # Starlette percent-encodes the filename per RFC 5987.
    disposition = response.headers["content-disposition"]
    assert "Waltz%20in%20C.mid" in disposition


def test_download_filename_is_sanitised(client):
    """Path separators and punctuation must never survive into a filename."""
    seed(client, "aaa", "and/or: a <sketch>")
    disposition = client.get("/api/sessions/aaa/download").headers["content-disposition"]
    name = disposition.split("''")[-1]
    for bad in ("/", "%2F", ":", "<", ">"):
        assert bad not in name, f"{bad!r} leaked into {name!r}"
    assert name.endswith(".mid")


def test_delete_removes_session_and_files(client):
    seed(client, "aaa", "One")
    directory = client.app.state.settings.sessions_dir / "aaa"
    assert directory.exists()

    client.delete("/api/sessions/aaa")
    assert client.get("/api/sessions/aaa").status_code == 404
    assert not directory.exists()


def test_missing_session_is_a_404(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/nope/notes").status_code == 404


# -- auth --------------------------------------------------------------------
def test_api_requires_login_when_a_password_is_set(auth_client):
    assert auth_client.get("/api/sessions").status_code == 401


def test_pages_redirect_to_login(auth_client):
    response = auth_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_with_the_right_password_grants_access(auth_client):
    bad = auth_client.post("/login", data={"password": "wrong", "next": "/"})
    assert bad.status_code == 401

    auth_client.post("/login", data={"password": "hunter2", "next": "/"})
    assert auth_client.get("/api/sessions").status_code == 200


def test_login_will_not_redirect_off_site(auth_client):
    response = auth_client.post(
        "/login", data={"password": "hunter2", "next": "https://evil.example/x"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def test_health_stays_public(auth_client):
    assert auth_client.get("/healthz").status_code == 200
