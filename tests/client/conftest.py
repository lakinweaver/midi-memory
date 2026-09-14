import pytest

from midi_memory.client.config import Settings
from midi_memory.client.spool import Spool


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "client",
        idle_seconds=45.0,
        min_notes=4,
        min_seconds=2.0,
        password="",
        server_url="",
        client_secret="",
        midi_source="none",
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def spool(settings) -> Spool:
    return Spool(settings)
