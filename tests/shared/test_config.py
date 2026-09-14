"""Settings shared by both halves, and the data directory they insist on."""
from __future__ import annotations

import os

import pytest

from midi_memory.client.config import Settings as ClientSettings
from midi_memory.server.config import Settings as ServerSettings
from midi_memory.shared.config import DataDirectoryError, make_dir

# As root every directory is writable, so there is no failure to observe.
needs_unprivileged = pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="root can write anywhere, so the permission path cannot be exercised",
)


@pytest.fixture
def read_only_dir(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    directory.chmod(0o500)
    yield directory
    directory.chmod(0o700)   # so pytest can clean up after itself


@needs_unprivileged
def test_an_unwritable_data_directory_says_who_owns_it(read_only_dir):
    """The bare PermissionError from pathlib names no cause, and the cause is
    always the same one: the process and the directory are different users."""
    with pytest.raises(DataDirectoryError) as caught:
        make_dir(read_only_dir / "sessions")

    message = str(caught.value)
    assert "Cannot write to the data directory" in message
    assert "belongs to uid" in message
    assert "PUID and PGID" in message, "the Docker fix has to be in the message"


@needs_unprivileged
def test_the_server_reports_it_rather_than_a_traceback(read_only_dir):
    """This is the Synology case: /data is a share owned by somebody else."""
    settings = ServerSettings(_env_file=None, data_dir=read_only_dir)
    with pytest.raises(DataDirectoryError):
        settings.ensure_dirs()


@needs_unprivileged
def test_the_client_reports_it_too(read_only_dir):
    settings = ClientSettings(_env_file=None, data_dir=read_only_dir)
    with pytest.raises(DataDirectoryError):
        settings.ensure_dirs()


def test_a_writable_directory_is_created_without_complaint(tmp_path):
    settings = ServerSettings(_env_file=None, data_dir=tmp_path / "fresh")
    settings.ensure_dirs()
    assert settings.sessions_dir.is_dir()

    settings.ensure_dirs()   # idempotent: this runs on every start
    assert settings.sessions_dir.is_dir()
