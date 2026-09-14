"""Settings common to both halves of the app.

The server and the client are separate processes on separate machines, so each
owns its own Settings class -- but they are both a small web app with a data
directory and an optional shared password, and that part is identical.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    """Shared defaults. Subclasses name the extra .env file they also read.

    Both halves answer to MIDI_MEMORY_*, because on a real install they are on
    different machines with a .env each and there is nothing to confuse. When
    they share a directory -- which is only ever during development -- the
    side-specific file settles it: later files win, so .env holds what they
    agree on and .env.server / .env.client hold what they do not.
    """

    model_config = SettingsConfigDict(
        env_prefix="MIDI_MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8080
    password: str = Field(
        default="",
        description="Shared password for the web UI. Empty disables the login.",
    )
    secret_key: str = Field(
        default="",
        description="Cookie signing key. Auto-generated into the data dir when unset.",
    )
    session_max_age_days: int = 90
    data_dir: Path = Path("data")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.password)

    def ensure_dirs(self) -> None:
        make_dir(self.data_dir)

    def resolve_secret_key(self) -> str:
        """Return the cookie signing key, generating a persistent one on first run."""
        if self.secret_key:
            return self.secret_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_file = self.data_dir / "secret_key"
        if not key_file.exists():
            key_file.write_text(os.urandom(32).hex(), encoding="utf-8")
            key_file.chmod(0o600)
        return key_file.read_text(encoding="utf-8").strip()


class DataDirectoryError(RuntimeError):
    """The data directory exists but cannot be written to."""


def make_dir(path: Path) -> Path:
    """Create a directory, explaining the one failure people actually hit.

    A bare PermissionError traceback out of pathlib says nothing about the cause,
    which is almost always that the process and the directory belong to different
    users -- the normal case when a container's data directory is a bind mount
    from a NAS share. Say whose it is and whose we are, since that is the whole
    answer.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise DataDirectoryError(_explain(path)) from exc
    return path


def _explain(path: Path) -> str:
    blocker = path
    while not blocker.exists() and blocker != blocker.parent:
        blocker = blocker.parent

    try:
        owner = f"uid {blocker.stat().st_uid}:gid {blocker.stat().st_gid}"
    except OSError:
        owner = "an unknown owner"
    try:
        who = f"uid {os.getuid()}:gid {os.getgid()} ({getpass.getuser()})"
    except (AttributeError, KeyError, OSError):
        who = "this process"

    return (
        f"Cannot write to the data directory {path}: {blocker} belongs to {owner} "
        f"and this is running as {who}.\n"
        f"In Docker, set PUID and PGID to the owner of the directory you mounted "
        f"-- on a NAS, `id <your-user>` over SSH will tell you what they are. "
        f"Otherwise, give {who} write access to {blocker}."
    )
