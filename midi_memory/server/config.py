"""Server settings, loaded from environment / .env.

The server never touches MIDI hardware, so none of the capture settings live
here -- those belong to the client, which does the recording.
"""
from __future__ import annotations

import logging
from datetime import datetime, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from midi_memory.shared.config import BaseAppSettings, make_dir

log = logging.getLogger(__name__)


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="MIDI_MEMORY_",
        env_file=(".env", ".env.server"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    timezone: str = Field(
        default="",
        description="IANA zone for session names and the date filter. Empty follows TZ.",
    )

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "midi-memory.db"

    @property
    def zone(self) -> Optional[tzinfo]:
        """The zone the server writes times in, or None to follow the process.

        The two places the server formats a time for a person -- the default
        session name and the date filter's day boundaries -- are the only ones
        that need this. Everything else in the UI is formatted by the browser in
        whatever zone the browser is in, which is already right.

        None is not a fallback to UTC: it means "hand astimezone() nothing",
        which follows TZ and re-reads it on every call, so DST is handled
        without the process having to be restarted twice a year.
        """
        if not self.timezone:
            return None
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            # Not fatal: a mistyped zone should not stop the library from
            # serving. It does go quietly wrong, though, so say so here and
            # again in Settings, where somebody comparing two clocks will look.
            log.warning("Unknown timezone %r; following TZ instead", self.timezone)
            return None

    @property
    def timezone_label(self) -> str:
        """What Settings shows: the zone actually in effect, not what was asked for."""
        following = datetime.now().astimezone().tzname() or "system default"
        if not self.timezone:
            return following
        if self.zone is None:
            return f"{self.timezone} (not found -- following {following})"
        return self.timezone

    def ensure_dirs(self) -> None:
        make_dir(self.sessions_dir)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
