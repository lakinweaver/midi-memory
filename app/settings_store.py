"""Runtime-editable settings, layered on top of the .env defaults.

The .env file is owned by the installer and only read when the service starts, so
editing it from the web UI would be both invasive and invisible until a restart.
Instead, overrides live in a small JSON file in the data directory and are applied
to the live Settings object, which every component reads from on each use -- so a
change takes effect on the next recorded note, with no restart.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import Settings

log = logging.getLogger(__name__)

OVERRIDE_FILENAME = "settings.json"

# Only these can be changed at runtime. Everything else (port, password, data
# directory, which MIDI backend) needs a restart, so the UI shows it read-only.
EDITABLE = (
    "idle_seconds",
    "min_notes",
    "min_seconds",
    "capture_during_playback",
    "device_match",
)


class SettingsStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def path(self):
        return self.settings.data_dir / OVERRIDE_FILENAME

    # -- persistence ---------------------------------------------------------
    def load(self) -> dict:
        """Apply any saved overrides to the live settings object."""
        if not self.path.exists():
            return {}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("Could not read %s; using .env values", self.path)
            return {}

        applied = {}
        for key, value in saved.items():
            if key in EDITABLE:
                setattr(self.settings, key, value)
                applied[key] = value
        if applied:
            log.info("Applied saved settings: %s", ", ".join(sorted(applied)))
        return applied

    def save(self, changes: dict[str, Any]) -> dict:
        """Apply changes to the live settings and persist them."""
        applied = {key: value for key, value in changes.items() if key in EDITABLE}
        for key, value in applied.items():
            setattr(self.settings, key, value)

        # Persist the full editable set, so the file is a complete picture rather
        # than a delta that is hard to reason about later.
        snapshot = {key: getattr(self.settings, key) for key in EDITABLE}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.replace(self.path)   # atomic: a power cut cannot leave a half-file
        return applied

    def reset(self) -> None:
        """Forget the overrides and fall back to whatever .env says."""
        self.path.unlink(missing_ok=True)

    # -- reporting -----------------------------------------------------------
    def current(self) -> dict:
        return {key: getattr(self.settings, key) for key in EDITABLE}

    def read_only(self) -> dict:
        """Shown in the UI for context, but only changeable in .env."""
        s = self.settings
        return {
            "host": s.host,
            "port": s.port,
            "data_dir": str(s.data_dir),
            "midi_source": s.midi_source,
            "midi_sink": s.midi_sink,
            "auth_enabled": s.auth_enabled,
            "overrides_active": self.path.exists(),
        }
