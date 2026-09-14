"""The piano sample set: reporting what is installed, and fetching what is not."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from midi_memory.server import samples
from midi_memory.server.main import create_app


def fake_sample(directory, name):
    (directory / f"{name}.mp3").write_bytes(b"x" * 2000)


def test_empty_directory_reports_nothing_installed(tmp_path):
    state = samples.status(tmp_path)
    assert state["installed"] == 0
    assert state["ready"] is False
    assert state["expected"] == len(samples.SAMPLES)


def test_samples_without_a_manifest_are_not_usable(tmp_path):
    """The player reads the manifest first, so files alone are not enough."""
    for name, _ in samples.SAMPLES[:5]:
        fake_sample(tmp_path, name)

    state = samples.status(tmp_path)
    assert state["installed"] == 5
    assert state["ready"] is False, "no manifest means the player cannot use them"

    samples.write_manifest(tmp_path)
    assert samples.status(tmp_path)["ready"] is True


def test_manifest_lists_only_what_is_actually_present(tmp_path):
    fake_sample(tmp_path, "C4")
    fake_sample(tmp_path, "A4")
    samples.write_manifest(tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert sorted(manifest["samples"].values()) == ["A4.mp3", "C4.mp3"]
    assert manifest["samples"]["60"] == "C4.mp3", "keyed by MIDI note number"


def test_truncated_files_do_not_count_as_installed(tmp_path):
    """A download cut off mid-file would otherwise look like a working sample."""
    (tmp_path / "C4.mp3").write_bytes(b"partial")
    assert samples.status(tmp_path)["installed"] == 0


def test_download_skips_what_is_already_there(tmp_path, monkeypatch):
    for name, _ in samples.SAMPLES:
        fake_sample(tmp_path, name)

    def explode(*_args, **_kwargs):
        raise AssertionError("should not re-download an existing sample")

    monkeypatch.setattr(samples, "_download_one", explode)
    result = samples.download(tmp_path)

    assert result["skipped"] == len(samples.SAMPLES)
    assert result["downloaded"] == 0
    assert result["ready"] is True


def test_download_reports_failures_without_giving_up(tmp_path, monkeypatch):
    calls = []

    def flaky(url, target):
        calls.append(target.name)
        if "A0" in target.name:
            return False
        target.write_bytes(b"x" * 2000)
        return True

    monkeypatch.setattr(samples, "_download_one", flaky)
    result = samples.download(tmp_path)

    assert result["failed"] == ["A0.mp3"]
    assert result["downloaded"] == len(samples.SAMPLES) - 1
    assert len(calls) == len(samples.SAMPLES), "one failure must not abort the rest"
    assert result["ready"] is True, "the rest are still usable"


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_settings_reports_sample_state(client):
    state = client.get("/api/settings").json()["samples"]
    assert {"installed", "expected", "ready"} <= set(state)


def test_download_endpoint_is_reachable_and_reports_back(client, monkeypatch):
    monkeypatch.setattr(samples, "download", lambda *a, **k: None)
    response = client.post("/api/settings/samples")
    assert response.status_code == 200
    assert "samples" in response.json()


def test_download_requires_login(settings):
    settings.password = "hunter2"
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/settings/samples").status_code == 401


def test_missing_manifest_is_rebuilt_from_what_is_on_disk(tmp_path):
    """The manifest is generated, so losing it must not cost you the samples."""
    for name, _ in samples.SAMPLES[:4]:
        fake_sample(tmp_path, name)
    samples.write_manifest(tmp_path)
    (tmp_path / "manifest.json").unlink()
    assert samples.status(tmp_path)["ready"] is False

    assert samples.ensure_manifest(tmp_path) is True
    assert samples.status(tmp_path)["ready"] is True


def test_rebuild_does_nothing_when_there_is_nothing_to_describe(tmp_path):
    assert samples.ensure_manifest(tmp_path) is False
    assert not (tmp_path / "manifest.json").exists()


def test_rebuild_leaves_an_existing_manifest_alone(tmp_path):
    fake_sample(tmp_path, "C4")
    (tmp_path / "manifest.json").write_text('{"format":"mp3","samples":{"60":"C4.mp3"}}')
    before = (tmp_path / "manifest.json").read_text()

    assert samples.ensure_manifest(tmp_path) is False
    assert (tmp_path / "manifest.json").read_text() == before


def test_app_startup_rebuilds_a_missing_manifest(settings, tmp_path, monkeypatch):
    """Exactly what happens on the Pi when a pull removes the generated file."""
    for name, _ in samples.SAMPLES[:3]:
        fake_sample(tmp_path, name)
    monkeypatch.setattr(samples, "audio_dir", lambda: tmp_path)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/settings").json()["samples"]["ready"] is True
    assert (tmp_path / "manifest.json").exists()
