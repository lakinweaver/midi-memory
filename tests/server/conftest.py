import pytest

from midi_memory.server.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(data_dir=tmp_path / "server", password="")
    s.ensure_dirs()
    return s
