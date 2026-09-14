"""Registering capture clients: secrets, revocation, and live status."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from midi_memory.server.clients import OFFLINE_AFTER_SECONDS, ClientError, ClientRegistry
from midi_memory.server.db import Database
from midi_memory.server.main import create_app
from midi_memory.shared.protocol import Heartbeat



@pytest.fixture
def registry(settings, clock) -> ClientRegistry:
    return ClientRegistry(Database(settings.db_path), clock=clock)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


# -- secrets -----------------------------------------------------------------
def test_registering_issues_a_secret_and_stores_only_its_hash(registry, settings):
    client, secret = registry.create("Upright")
    assert len(secret) > 30, "long enough that guessing is not a strategy"
    assert client["name"] == "Upright"

    # Nothing that comes back out of the registry carries the secret or its hash.
    assert "secret_hash" not in client
    assert secret not in str(registry.listing())

    with Database(settings.db_path).connect() as conn:
        stored = conn.execute("SELECT secret_hash FROM clients").fetchone()["secret_hash"]
    assert secret not in stored


def test_the_secret_authenticates_and_nothing_else_does(registry):
    client, secret = registry.create("Upright")

    assert registry.authenticate(secret)["id"] == client["id"]
    assert registry.authenticate("") is None
    assert registry.authenticate(secret + "x") is None
    assert registry.authenticate(secret.upper()) is None


def test_rotating_replaces_the_old_secret_immediately(registry):
    _client, old = registry.create("Upright")
    new = registry.rotate_secret(_client["id"])

    assert registry.authenticate(old) is None, "a rotated secret stops working at once"
    assert registry.authenticate(new) is not None


def test_a_revoked_client_cannot_authenticate_but_keeps_its_name(registry):
    client, secret = registry.create("Upright")
    registry.set_revoked(client["id"], True)

    assert registry.authenticate(secret) is None
    assert registry.db.get_client(client["id"])["name"] == "Upright"

    registry.set_revoked(client["id"], False)
    assert registry.authenticate(secret) is not None, "restoring brings it back"


def test_two_clients_cannot_share_a_name(registry):
    registry.create("Upright")
    with pytest.raises(ClientError):
        registry.create("upright")


# -- live status -------------------------------------------------------------
def test_a_client_starts_offline_until_it_reports(registry):
    client, _secret = registry.create("Upright")
    assert registry.status(client["id"])["state"] == "offline"

    registry.heartbeat(client["id"], Heartbeat(state="recording", connected=True,
                                               port_name="Yamaha", note_count=12))
    status = registry.status(client["id"])
    assert status["state"] == "recording"
    assert status["port_name"] == "Yamaha"
    assert status["note_count"] == 12


def test_a_client_that_stops_reporting_is_swept_offline(registry, clock):
    client, _secret = registry.create("Upright")
    registry.heartbeat(client["id"], Heartbeat(state="idle", connected=True))
    assert registry.sweep() == [], "still within the grace period"

    clock.advance(OFFLINE_AFTER_SECONDS + 1)
    assert registry.sweep() == [client["id"]]
    assert registry.status(client["id"])["state"] == "offline"
    assert registry.status(client["id"])["connected"] is False


# -- deleting ----------------------------------------------------------------
def test_a_client_with_recordings_cannot_be_deleted(registry, seeded_session):
    client_id = seeded_session
    with pytest.raises(ClientError, match="Revoke it instead"):
        registry.delete(client_id)


def test_a_client_with_no_recordings_can_be_deleted(registry):
    client, _secret = registry.create("Upright")
    registry.delete(client["id"])
    assert registry.db.get_client(client["id"]) is None


@pytest.fixture
def seeded_session(registry, settings):
    """A client that owns one recording, so deletion has something to refuse."""
    from datetime import datetime, timedelta, timezone

    from midi_memory.shared.protocol import SessionUpload

    client, _secret = registry.create("Upright")
    started = datetime.now(timezone.utc)
    registry.db.insert_session(
        SessionUpload(id="abcdef0123456789", started_at=started,
                      ended_at=started + timedelta(seconds=4), duration_ms=4000,
                      event_count=8, note_count=4),
        client_id=client["id"],
    )
    return client["id"]


# -- the HTTP surface --------------------------------------------------------
def test_the_secret_is_returned_once_and_never_again(client):
    created = client.post("/api/clients", json={"name": "Upright"})
    assert created.status_code == 201
    secret = created.json()["secret"]
    assert secret

    listed = client.get("/api/clients").json()["clients"]
    assert len(listed) == 1
    assert "secret" not in listed[0] and "secret_hash" not in listed[0]
    assert secret not in listed[0].values()


def test_a_duplicate_name_is_refused_with_a_readable_reason(client):
    client.post("/api/clients", json={"name": "Upright"})
    clash = client.post("/api/clients", json={"name": "Upright"})
    assert clash.status_code == 409
    assert "already a client" in clash.json()["detail"]


def test_renaming_and_revoking_over_http(client):
    client_id = client.post("/api/clients", json={"name": "Upright"}).json()["client"]["id"]

    renamed = client.patch(f"/api/clients/{client_id}", json={"name": "Grand"})
    assert renamed.json()["client"]["name"] == "Grand"

    revoked = client.post(f"/api/clients/{client_id}/revoke")
    assert revoked.json()["client"]["revoked"] is True

    restored = client.post(f"/api/clients/{client_id}/restore")
    assert restored.json()["client"]["revoked"] is False


def test_client_management_requires_login(settings):
    settings.password = "hunter2"
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/clients").status_code == 401
        assert c.post("/api/clients", json={"name": "Upright"}).status_code == 401
