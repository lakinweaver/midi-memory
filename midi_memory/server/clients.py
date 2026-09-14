"""The registry of capture clients: who may upload, and what each is doing.

Secrets are stored only as a SHA-256 hash. That is deliberately a *fast* hash
rather than bcrypt or argon2: these are 256-bit tokens straight out of the
system CSPRNG, not passwords a person chose, so there is no dictionary to run
and nothing for a slow KDF to buy. It also means the hash can be an indexed
column, so authenticating an upload is one lookup rather than a table scan
against every registered client.

Live state -- idle, recording, offline -- is held in memory and never written to
SQLite. A heartbeat every five seconds would otherwise be a database write every
five seconds, forever, to record something that is worthless the moment the
process restarts. Only `last_seen_at` is persisted, and only occasionally.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from midi_memory.server.db import Database
from midi_memory.server.events import EventBus
from midi_memory.shared.protocol import Heartbeat

log = logging.getLogger(__name__)

# A client is assumed gone if it misses several heartbeats in a row, rather than
# one: a Pi on wifi drops the occasional packet without having gone anywhere.
OFFLINE_AFTER_SECONDS = 20.0
SWEEP_SECONDS = 5.0
PERSIST_LAST_SEEN_EVERY = 60.0

NAME_MAX = 40


class ClientError(Exception):
    """Something the caller asked for that cannot be done, with a reason to show."""


@dataclass
class LiveStatus:
    state: str = "offline"
    connected: bool = False
    port_name: str = ""
    note_count: int = 0
    pending_uploads: int = 0
    uptime_seconds: int = 0
    last_seen: float = 0.0
    persisted_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "connected": self.connected,
            "port_name": self.port_name,
            "note_count": self.note_count,
            "pending_uploads": self.pending_uploads,
            "uptime_seconds": self.uptime_seconds,
        }


class ClientRegistry:
    def __init__(self, db: Database, bus: Optional[EventBus] = None,
                 clock=time.monotonic) -> None:
        self.db = db
        self.bus = bus
        self.clock = clock
        self._live: dict[str, LiveStatus] = {}

    # -- registration --------------------------------------------------------
    def create(self, name: str) -> tuple[dict, str]:
        """Register a client. Returns the record and its secret, shown once."""
        name = _clean_name(name)
        if self.db.get_client_by_name(name) is not None:
            raise ClientError(f"There is already a client called “{name}”.")
        client_id = uuid.uuid4().hex[:12]
        secret = _new_secret()
        client = self.db.create_client(client_id, name, _hash(secret))
        log.info("Registered client %s (%s)", name, client_id)
        return client, secret

    def rotate_secret(self, client_id: str) -> str:
        """Issue a new secret, invalidating the old one immediately."""
        if self.db.get_client(client_id) is None:
            raise ClientError("No such client.")
        secret = _new_secret()
        self.db.update_client(client_id, secret_hash=_hash(secret), revoked=0)
        log.info("Rotated the secret for client %s", client_id)
        return secret

    def rename(self, client_id: str, name: str) -> dict:
        name = _clean_name(name)
        existing = self.db.get_client_by_name(name)
        if existing is not None and existing["id"] != client_id:
            raise ClientError(f"There is already a client called “{name}”.")
        if not self.db.update_client(client_id, name=name):
            raise ClientError("No such client.")
        return self.db.get_client(client_id)

    def set_revoked(self, client_id: str, revoked: bool) -> dict:
        """Stop (or resume) accepting uploads, without losing attribution."""
        if not self.db.update_client(client_id, revoked=int(revoked)):
            raise ClientError("No such client.")
        if revoked:
            self._live.pop(client_id, None)
            self._publish(client_id)
        return self.db.get_client(client_id)

    def delete(self, client_id: str) -> None:
        count = self.db.client_session_count(client_id)
        if count:
            raise ClientError(
                f"That client has {count} recording{'' if count == 1 else 's'} in the "
                "library. Revoke it instead, so its recordings keep their source."
            )
        self.db.delete_client(client_id)
        self._live.pop(client_id, None)

    # -- authentication ------------------------------------------------------
    def authenticate(self, secret: str) -> Optional[dict]:
        """The client behind this secret, or None. Revoked clients do not match."""
        if not secret:
            return None
        client = self.db.find_client_by_secret_hash(_hash(secret))
        if client is None or client["revoked"]:
            return None
        return client

    # -- live status ---------------------------------------------------------
    def heartbeat(self, client_id: str, beat: Heartbeat) -> None:
        status = self._live.setdefault(client_id, LiveStatus())
        before = status.state
        status.state = beat.state if beat.state != "offline" else "idle"
        status.connected = beat.connected
        status.port_name = beat.port_name
        status.note_count = beat.note_count
        status.pending_uploads = beat.pending_uploads
        status.uptime_seconds = beat.uptime_seconds
        status.last_seen = self.clock()

        now = self.clock()
        if now - status.persisted_at > PERSIST_LAST_SEEN_EVERY:
            status.persisted_at = now
            self.db.update_client(client_id, last_seen_at=_utc_now_iso())

        if status.state != before or before == "offline":
            self._publish(client_id)

    def sweep(self) -> list[str]:
        """Mark clients that have stopped reporting as offline. Returns which."""
        now = self.clock()
        gone = []
        for client_id, status in self._live.items():
            if status.state != "offline" and now - status.last_seen > OFFLINE_AFTER_SECONDS:
                status.state = "offline"
                status.connected = False
                gone.append(client_id)
                self._publish(client_id)
        return gone

    def status(self, client_id: str) -> dict:
        return self._live.get(client_id, LiveStatus()).as_dict()

    def listing(self) -> list[dict]:
        """Every registered client, with its live state folded in."""
        return [{**client, **self.status(client["id"])}
                for client in self.db.list_clients()]

    def count(self) -> int:
        return len(self.db.list_clients())

    # -- events --------------------------------------------------------------
    def _publish(self, client_id: str) -> None:
        if self.bus is None:
            return
        client = self.db.get_client(client_id)
        if client is None:
            return
        self.bus.publish("client_status", client_id=client_id,
                         name=client["name"], **self.status(client_id))


# -- helpers -----------------------------------------------------------------
def _new_secret() -> str:
    return secrets.token_urlsafe(32)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())[:NAME_MAX].strip()
    if not cleaned:
        raise ClientError("A client needs a name.")
    return cleaned


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
