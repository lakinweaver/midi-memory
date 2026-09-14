import pytest

from midi_memory.server.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    # See the client fixture: tests never read the developer's .env.
    s = Settings(_env_file=None, data_dir=tmp_path / "server", password="")
    s.ensure_dirs()
    return s
