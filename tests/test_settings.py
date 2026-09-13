"""Runtime-editable settings: persistence, live effect, and guard rails."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.settings_store import SettingsStore


@pytest.fixture
def client(settings):
    settings.midi_source = "none"
    settings.midi_sink = "mock"
    with TestClient(create_app(settings)) as c:
        yield c


def test_defaults_are_reported(client):
    payload = client.get("/api/settings").json()
    assert payload["settings"]["idle_seconds"] == 45.0
    assert payload["settings"]["min_notes"] == 4
    assert payload["read_only"]["midi_sink"] == "mock"


def test_saving_changes_the_running_recorder(client):
    """A setting that only lands after a restart would be a trap."""
    client.put("/api/settings", json={"idle_seconds": 12.5})

    assert client.app.state.settings.idle_seconds == 12.5
    assert client.app.state.service.settings.idle_seconds == 12.5
    assert client.app.state.service.recorder.settings.idle_seconds == 12.5


def test_capture_during_playback_takes_effect_immediately(client):
    service = client.app.state.service
    client.put("/api/settings", json={"capture_during_playback": True})
    assert service.settings.capture_during_playback is True
    assert service._suppress_capture() is False, "opting in disables suppression at once"


def test_settings_survive_a_restart(settings):
    settings.midi_source = settings.midi_sink = "none"
    with TestClient(create_app(settings)) as c:
        c.put("/api/settings", json={"idle_seconds": 30, "min_notes": 9})

    fresh = Settings(data_dir=settings.data_dir)
    assert fresh.idle_seconds == 45.0, "a new object starts from .env"
    SettingsStore(fresh).load()
    assert fresh.idle_seconds == 30.0 and fresh.min_notes == 9


def test_reset_clears_the_overrides(client, settings):
    client.put("/api/settings", json={"idle_seconds": 30})
    assert (settings.data_dir / "settings.json").exists()

    client.post("/api/settings/reset")
    assert not (settings.data_dir / "settings.json").exists()


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


def test_settings_require_login(auth_settings):
    with TestClient(create_app(auth_settings)) as c:
        assert c.get("/api/settings").status_code == 401
        assert c.put("/api/settings", json={"idle_seconds": 10}).status_code == 401


@pytest.fixture
def auth_settings(settings):
    settings.midi_source = settings.midi_sink = "none"
    settings.password = "hunter2"
    return settings
