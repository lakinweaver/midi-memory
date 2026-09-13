"""Application settings, loaded from environment / .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MIDI_MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- server ---
    host: str = "0.0.0.0"
    port: int = 8080
    password: str = Field(
        default="",
        description="Shared password for the web UI. Empty disables auth (not recommended).",
    )
    secret_key: str = Field(
        default="",
        description="Cookie signing key. Auto-generated into the data dir when unset.",
    )
    session_max_age_days: int = 90

    # --- storage ---
    data_dir: Path = Path("data")

    # --- capture / segmentation ---
    idle_seconds: float = Field(
        default=45.0,
        description="Silence, in seconds, that ends a recording session.",
    )
    min_notes: int = Field(
        default=4,
        description="Sessions with fewer note-ons than this are discarded as accidental.",
    )
    min_seconds: float = Field(
        default=2.0,
        description="Sessions shorter than this are discarded as accidental.",
    )
    device_match: str = Field(
        default="",
        description="Substring of the MIDI port name to record from. Empty = first suitable port.",
    )

    # --- source selection ---
    midi_source: str = Field(
        default="auto",
        description="One of: auto, alsa, portable, mock, none.",
    )

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "midi-memory.db"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.password)

    def ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
