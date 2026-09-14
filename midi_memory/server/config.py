"""Server settings, loaded from environment / .env.

The server never touches MIDI hardware, so none of the capture settings live
here -- those belong to the client, which does the recording.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from midi_memory.shared.config import BaseAppSettings


class Settings(BaseAppSettings):
    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "midi-memory.db"

    def ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
