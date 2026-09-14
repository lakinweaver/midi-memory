"""The client's settings page: persistence, live effect, and guard rails."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from midi_memory.client.config import Settings
from midi_memory.client.main import create_app
from midi_memory.client.settings_store import SettingsStore


@pytest.fixture
def client(settings):
    # The capture loop is not what these tests are about, and starting it would
    # tie every assertion to a real one-second tick.
    with TestClient(create_app(settings, start_capture=False)) as c:
        yield c


def test_defaults_are_reported(client):
    payload = client.get("/api/settings").json()
    assert payload["settings"]["idle_seconds"] == 45.0
    assert payload["settings"]["min_notes"] == 4
    assert payload["read_only"]["midi_source"] == "none"


def test_status_reports_the_recorder_and_the_link(client):
    payload = client.get("/api/status").json()
    assert payload["status"]["recording"] is False
    assert payload["link"]["pending"] == 0
    assert payload["link"]["authenticated"] is False


def test_saving_changes_the_running_recorder(client):
    """A setting that only lands after a restart would be a trap."""
    client.put("/api/settings", json={"idle_seconds": 12.5})

    assert client.app.state.settings.idle_seconds == 12.5
    assert client.app.state.service.settings.idle_seconds == 12.5
    assert client.app.state.service.recorder.settings.idle_seconds == 12.5


def test_settings_survive_a_restart(settings):
    with TestClient(create_app(settings, start_capture=False)) as c:
        c.put("/api/settings", json={"idle_seconds": 30, "min_notes": 9})

    fresh = Settings(data_dir=settings.data_dir)
    assert fresh.idle_seconds == 45.0, "a new object starts from .env"
    SettingsStore(fresh).load()
    assert fresh.idle_seconds == 30.0 and fresh.min_notes == 9


@pytest.mark.parametrize("payload", [
    {"idle_seconds": 0.5},      # would chop one phrase into fragments
    {"idle_seconds": 99999},    # would glue a whole evening together
    {"min_notes": -1},
    {"min_notes": 5000},
    {"min_seconds": -3},
])
def test_nonsense_values_are_rejected(client, payload):
    assert client.put("/api/settings", json=payload).status_code == 422


def test_partial_updates_leave_other_settings_alone(client):
    client.put("/api/settings", json={"idle_seconds": 20})
    client.put("/api/settings", json={"min_notes": 7})

    current = client.get("/api/settings").json()["settings"]
    assert current["idle_seconds"] == 20.0, "the earlier change must survive"
    assert current["min_notes"] == 7


def test_device_filter_is_trimmed(client):
    saved = client.put("/api/settings", json={"device_match": "  Yamaha  "}).json()
    assert saved["settings"]["device_match"] == "Yamaha"


# -- the link to the server --------------------------------------------------
def test_server_url_loses_its_trailing_slash(client):
    """Otherwise every request path would end up with a double slash in it."""
    saved = client.put("/api/settings",
                       json={"server_url": "http://box.local:8080/"}).json()
    assert saved["settings"]["server_url"] == "http://box.local:8080"


def test_the_secret_is_never_sent_back_to_the_browser(client):
    saved = client.put("/api/settings", json={"client_secret": "s3cr3t"}).json()
    assert saved["settings"]["client_secret"] is True, "only whether one is set"
    assert "s3cr3t" not in saved["settings"].values()
    assert client.app.state.settings.client_secret == "s3cr3t"


def test_an_empty_secret_field_keeps_the_saved_one(client):
    """The page cannot show the secret, so blank has to mean 'leave it alone'."""
    client.put("/api/settings", json={"client_secret": "s3cr3t"})
    client.put("/api/settings", json={"client_secret": "", "idle_seconds": 20})

    assert client.app.state.settings.client_secret == "s3cr3t"
    assert client.app.state.settings.idle_seconds == 20


def test_the_secret_file_is_not_world_readable(client, settings):
    client.put("/api/settings", json={"client_secret": "s3cr3t"})
    mode = (settings.data_dir / "settings.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_settings_require_login(auth_settings):
    with TestClient(create_app(auth_settings, start_capture=False)) as c:
        assert c.get("/api/settings").status_code == 401
        assert c.put("/api/settings", json={"idle_seconds": 10}).status_code == 401


@pytest.fixture
def auth_settings(settings):
    settings.password = "hunter2"
    return settings
