import pytest

from midi_memory.client.config import Settings
from midi_memory.client.spool import Spool


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None: a test must not read whatever .env the developer happens
    # to have lying around, or its defaults change from machine to machine.
    s = Settings(
        _env_file=None,
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
