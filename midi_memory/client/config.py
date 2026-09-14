"""Client settings, loaded from environment / .env.

Only two of these have to be filled in for a working install -- the server
address and the client secret -- and both can be set from the client's own web
page, so a Raspberry Pi never needs a text editor. Everything else has a
default that suits a piano in a living room.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field

from midi_memory.shared.config import BaseAppSettings


class Settings(BaseAppSettings):
    # The server owns 8080; the client sits next to it so both can run on one
    # machine during development without a port clash.
    port: int = 8081

    # --- link to the server ---
    server_url: str = Field(
        default="",
        description="Base URL of the MIDI Memory server, e.g. http://192.168.1.20:8080",
    )
    client_secret: str = Field(
        default="",
        description="Secret issued by the server when this client was registered.",
    )
    client_name: str = Field(
        default="",
        description="Name to register under. Empty uses the machine's hostname.",
    )

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
    midi_source: str = Field(
        default="auto",
        description="One of: auto, alsa, portable, mock, none.",
    )

    # --- upload spool ---
    keep_uploaded_days: int = Field(
        default=7,
        description="Days to keep a local copy after the server confirms an upload. "
                    "0 deletes immediately.",
    )

    @property
    def spool_dir(self) -> Path:
        """Where sessions are recorded and wait to be uploaded."""
        return self.data_dir / "spool"

    @property
    def uploaded_dir(self) -> Path:
        """Sessions the server has confirmed, kept for `keep_uploaded_days`."""
        return self.data_dir / "uploaded"

    @property
    def linked(self) -> bool:
        return bool(self.server_url and self.client_secret)

    def ensure_dirs(self) -> None:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.uploaded_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
