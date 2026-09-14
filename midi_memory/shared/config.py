"""Settings common to both halves of the app.

The server and the client are separate processes on separate machines, so each
owns its own Settings class -- but they are both a small web app with a data
directory and an optional shared password, and that part is identical.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
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
        self.data_dir.mkdir(parents=True, exist_ok=True)

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
