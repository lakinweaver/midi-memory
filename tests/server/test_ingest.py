"""The door clients come in by: authentication, storage, and attribution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from midi_memory.server.main import create_app
from midi_memory.shared.midi.events import MidiEvent
from midi_memory.shared.midi.smf import write_smf
from midi_memory.shared.protocol import MAX_UPLOAD_BYTES, SessionUpload


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def registered(client):
    """A registered client and its secret, as a real Pi would have been given."""
    body = client.post("/api/clients", json={"name": "Upright"}).json()
    return body["client"], body["secret"]


def midi_bytes(tmp_path, notes: int = 6) -> bytes:
    events = []
    for i in range(notes):
        events.append(MidiEvent(i * 0.5, 0x90, 60 + i, 80))
        events.append(MidiEvent(i * 0.5 + 0.4, 0x80, 60 + i, 0))
    path = tmp_path / "session.mid"
    write_smf(events, path, name="test")
    return path.read_bytes()


def payload(session_id: str = "abcdef0123456789", notes: int = 6) -> str:
    started = datetime.now(timezone.utc)
    return SessionUpload(
        id=session_id, started_at=started,
        ended_at=started + timedelta(seconds=3), duration_ms=3000,
        event_count=notes * 2, note_count=notes, lowest_note=60,
        highest_note=60 + notes - 1, avg_velocity=80.0,
        device_name="Yamaha P-125", fingerprint=[[0, 60, 10, 80]],
    ).model_dump_json()


def upload(client, secret, tmp_path, session_id="abcdef0123456789", notes=6,
           events_file=False):
    files = {"midi": ("session.mid", midi_bytes(tmp_path, notes), "audio/midi")}
    if events_file:
        files["events"] = ("events.jsonl", b'{"t":0,"s":144,"a":60,"b":80}\n',
                           "application/x-ndjson")
    return client.post(
        "/api/ingest/sessions",
        headers={"Authorization": f"Bearer {secret}"},
        data={"metadata": payload(session_id, notes)},
        files=files,
    )


# -- authentication ----------------------------------------------------------
def test_an_upload_without_a_secret_is_refused(client, tmp_path):
    response = client.post(
        "/api/ingest/sessions",
        data={"metadata": payload()},
        files={"midi": ("session.mid", midi_bytes(tmp_path), "audio/midi")},
    )
    assert response.status_code == 401


def test_an_unknown_secret_is_refused(client, tmp_path):
    assert upload(client, "not-a-real-secret", tmp_path).status_code == 403


def test_a_revoked_client_is_refused(client, registered, tmp_path):
    registered_client, secret = registered
    client.post(f"/api/clients/{registered_client['id']}/revoke")
    assert upload(client, secret, tmp_path).status_code == 403


def test_ingest_is_reachable_even_when_the_browser_ui_needs_a_password(settings, tmp_path):
    """A Pi has no cookie, and should not need one to deliver a recording."""
    settings.password = "hunter2"
    with TestClient(create_app(settings)) as c:
        # Registration happens in the browser, so do it through the app directly.
        _client, secret = c.app.state.clients.create("Upright")
        assert upload(c, secret, tmp_path).status_code == 201
        assert c.post("/api/ingest/hello",
                      headers={"Authorization": f"Bearer {secret}"}).status_code == 200


def test_hello_names_the_client_back(client, registered):
    registered_client, secret = registered
    body = client.post("/api/ingest/hello",
                       headers={"Authorization": f"Bearer {secret}"}).json()
    assert body["ok"] is True
    assert body["client_name"] == "Upright"
    assert body["client_id"] == registered_client["id"]


# -- storing -----------------------------------------------------------------
def test_an_upload_lands_in_the_library_attributed_to_its_client(client, registered,
                                                                 settings, tmp_path):
    registered_client, secret = registered
    assert upload(client, secret, tmp_path, notes=6).status_code == 201

    result = client.get("/api/sessions").json()
    assert result["total"] == 1
    session = result["items"][0]
    assert session["note_count"] == 6
    assert session["client_id"] == registered_client["id"]
    assert session["client_name"] == "Upright", "the library shows where it came from"
    assert session["device_name"] == "Yamaha P-125"
    assert session["name"], "a session gets a human-readable default name"

    stored = settings.sessions_dir / "abcdef0123456789" / "session.mid"
    assert stored.exists() and stored.stat().st_size > 0


def test_the_event_log_is_kept_when_the_client_sends_it(client, registered,
                                                        settings, tmp_path):
    _registered_client, secret = registered
    upload(client, secret, tmp_path, events_file=True)
    assert (settings.sessions_dir / "abcdef0123456789" / "events.jsonl").exists()


def test_an_uploaded_session_is_playable_and_downloadable(client, registered, tmp_path):
    _registered_client, secret = registered
    upload(client, secret, tmp_path, notes=6)

    notes = client.get("/api/sessions/abcdef0123456789/notes")
    assert notes.status_code == 200
    assert len(notes.json()["notes"]) == 6

    download = client.get("/api/sessions/abcdef0123456789/download")
    assert download.status_code == 200
    assert download.content[:4] == b"MThd"


def test_re_uploading_the_same_session_is_idempotent(client, registered, tmp_path):
    """A lost acknowledgement must not file the same take twice."""
    _registered_client, secret = registered
    assert upload(client, secret, tmp_path).json()["status"] == "stored"

    again = upload(client, secret, tmp_path)
    assert again.status_code == 200
    assert again.json()["status"] == "duplicate"
    assert client.get("/api/sessions").json()["total"] == 1


def test_another_clients_session_id_is_refused_rather_than_overwritten(client, registered,
                                                                      tmp_path):
    _registered_client, secret = registered
    upload(client, secret, tmp_path)

    body = client.post("/api/clients", json={"name": "Grand"}).json()
    clash = upload(client, body["secret"], tmp_path)

    assert clash.status_code == 409
    assert client.get("/api/sessions").json()["items"][0]["client_name"] == "Upright"


def test_nonsense_metadata_is_rejected(client, registered, tmp_path):
    _registered_client, secret = registered
    response = client.post(
        "/api/ingest/sessions",
        headers={"Authorization": f"Bearer {secret}"},
        data={"metadata": '{"id": "x", "note_count": -5}'},
        files={"midi": ("session.mid", midi_bytes(tmp_path), "audio/midi")},
    )
    assert response.status_code == 422


def test_an_oversized_recording_is_refused(client, registered, tmp_path):
    _registered_client, secret = registered
    response = client.post(
        "/api/ingest/sessions",
        headers={"Authorization": f"Bearer {secret}"},
        data={"metadata": payload()},
        files={"midi": ("session.mid", b"M" * (MAX_UPLOAD_BYTES + 1), "audio/midi")},
    )
    assert response.status_code == 413


def test_a_failed_store_leaves_nothing_behind(client, registered, settings,
                                              tmp_path, monkeypatch):
    """A directory with no row for it would confuse everything that came later."""
    _registered_client, secret = registered

    def explode(*_args, **_kwargs):
        raise RuntimeError("disk gave out")

    monkeypatch.setattr(client.app.state.db, "insert_session", explode)
    assert upload(client, secret, tmp_path).status_code == 500
    assert not (settings.sessions_dir / "abcdef0123456789").exists()


# -- heartbeats --------------------------------------------------------------
def test_a_heartbeat_shows_up_as_client_status(client, registered):
    registered_client, secret = registered
    response = client.post(
        "/api/ingest/heartbeat",
        headers={"Authorization": f"Bearer {secret}"},
        json={"state": "recording", "connected": True,
              "port_name": "Yamaha P-125", "note_count": 17, "pending_uploads": 2},
    )
    assert response.status_code == 200

    listed = client.get("/api/status").json()["clients"]
    assert listed[0]["id"] == registered_client["id"]
    assert listed[0]["state"] == "recording"
    assert listed[0]["note_count"] == 17
    assert listed[0]["pending_uploads"] == 2


def test_the_library_can_be_filtered_to_one_client(client, registered, tmp_path):
    registered_client, secret = registered
    upload(client, secret, tmp_path, session_id="aaaaaaaaaaaaaaaa")

    other = client.post("/api/clients", json={"name": "Grand"}).json()
    upload(client, other["secret"], tmp_path, session_id="bbbbbbbbbbbbbbbb")

    mine = client.get(f"/api/sessions?client_id={registered_client['id']}").json()
    assert mine["total"] == 1
    assert mine["items"][0]["id"] == "aaaaaaaaaaaaaaaa"
