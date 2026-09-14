"""Carries spooled recordings to the server, and reports this client's state.

Designed around the assumption that the server is *usually* reachable and
*sometimes* not. Nothing here is allowed to lose a take: a session leaves the
spool only once the server has said, in so many words, that it has it.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from midi_memory.client.config import Settings
from midi_memory.client.spool import Spool, SpooledSession
from midi_memory.shared.protocol import Heartbeat, MAX_UPLOAD_BYTES

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5.0
IDLE_POLL_SECONDS = 30.0     # a safety net; uploads are normally nudged, not polled
BACKOFF_START = 2.0
BACKOFF_MAX = 300.0
REQUEST_TIMEOUT = 60.0


@dataclass
class LinkState:
    """What the settings page shows about the connection to the server."""

    reachable: bool = False
    authenticated: bool = False
    last_error: str = ""
    last_upload_at: Optional[float] = None
    last_contact_at: Optional[float] = None
    uploaded_count: int = 0
    server_name: str = ""

    def as_dict(self, pending: int) -> dict:
        return {
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "last_error": self.last_error,
            "last_upload_at": self.last_upload_at,
            "last_contact_at": self.last_contact_at,
            "uploaded_count": self.uploaded_count,
            "pending": pending,
        }


class Uploader:
    def __init__(self, settings: Settings, spool: Spool,
                 client_factory=None, status_source=None) -> None:
        self.settings = settings
        self.spool = spool
        self.state = LinkState()
        # Injectable so the tests can point a real client at a real server
        # running in-process, rather than mocking out the transport.
        self._client_factory = client_factory or self._default_client
        self._status_source = status_source
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._backoff = BACKOFF_START

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._drain_loop(), name="upload-drain"),
            asyncio.create_task(self._heartbeat_loop(), name="upload-heartbeat"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def nudge(self, *_args) -> None:
        """A session just finished: drain now rather than at the next poll."""
        self._wake.set()

    # -- plumbing ------------------------------------------------------------
    def _default_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.server_url.rstrip("/"),
            timeout=REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {self.settings.client_secret}"},
            follow_redirects=False,
        )

    # -- loops ---------------------------------------------------------------
    async def _drain_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=IDLE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

            if not self.settings.linked:
                continue
            try:
                sent = await self.drain()
            except Exception:
                log.exception("Upload pass failed")
                sent = 0
            if sent == 0 and self.spool.pending_count():
                # Something is wrong and retrying immediately will not fix it.
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, BACKOFF_MAX)
                self._wake.set()
            else:
                self._backoff = BACKOFF_START

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not self.settings.linked:
                continue
            try:
                await self.heartbeat()
            except Exception:
                log.debug("Heartbeat failed", exc_info=True)

    # -- work ----------------------------------------------------------------
    async def drain(self) -> int:
        """Upload everything pending, oldest first. Returns how many went up."""
        pending = self.spool.pending()
        if not pending:
            return 0
        sent = 0
        async with self._client_factory() as client:
            for session in pending:
                if not await self._upload_one(client, session):
                    break   # stop at the first failure; order is the point
                sent += 1
        return sent

    async def _upload_one(self, client: httpx.AsyncClient,
                          session: SpooledSession) -> bool:
        try:
            payload = session.payload()
        except (OSError, ValueError):
            log.exception("Unreadable spool entry %s; leaving it in place", session.id)
            return False

        midi_bytes = session.midi_path.read_bytes()
        if len(midi_bytes) > MAX_UPLOAD_BYTES:
            log.error("Session %s is %d bytes, over the server's limit; skipping",
                      session.id, len(midi_bytes))
            return False

        files = [("midi", ("session.mid", midi_bytes, "audio/midi"))]
        if session.events_path.exists():
            files.append(("events", ("events.jsonl",
                                     session.events_path.read_bytes(),
                                     "application/x-ndjson")))
        try:
            response = await client.post(
                "/api/ingest/sessions",
                data={"metadata": payload.model_dump_json()},
                files=files,
            )
        except httpx.HTTPError as exc:
            self._note_failure(f"Cannot reach the server: {exc.__class__.__name__}")
            return False

        if response.status_code in (200, 201):
            self.spool.complete(session.id)
            self.state.reachable = True
            self.state.authenticated = True
            self.state.last_error = ""
            self.state.last_upload_at = time.time()
            self.state.last_contact_at = time.time()
            self.state.uploaded_count += 1
            log.info("Uploaded session %s (%s)", session.id,
                     response.json().get("status", "stored"))
            return True

        if response.status_code in (401, 403):
            # Retrying cannot help: the secret is wrong or the client is revoked.
            # Say so on the settings page instead of hammering the server.
            self.state.reachable = True
            self.state.authenticated = False
            self.state.last_error = (
                "The server rejected this client's secret. Re-issue it on the "
                "server's Clients page and paste the new one below."
            )
            log.error("Upload rejected (%d): %s", response.status_code, self.state.last_error)
            return False

        if response.status_code == 409:
            # This id belongs to a different client. Ours is a local name for a
            # local file, so renaming it is harmless and unblocks the queue.
            log.warning("Session id %s is taken on the server; re-filing it", session.id)
            self._rename_conflict(session)
            return False

        self._note_failure(f"Server refused the upload ({response.status_code})")
        return False

    def _rename_conflict(self, session: SpooledSession) -> None:
        import uuid

        new_id = uuid.uuid4().hex[:16]
        target = session.directory.parent / new_id
        try:
            payload = session.payload()
            session.directory.replace(target)
            renamed = payload.model_copy(update={"id": new_id})
            (target / "upload.json").write_text(
                renamed.model_dump_json(indent=2), encoding="utf-8")
        except (OSError, ValueError):
            log.exception("Could not re-file session %s after an id conflict", session.id)

    async def heartbeat(self) -> bool:
        status = self._status_source() if self._status_source else {}
        beat = Heartbeat(
            state=status.get("state", "idle"),
            connected=bool(status.get("connected", False)),
            port_name=status.get("port_name", "") or "",
            device_name=status.get("port_name", "") or "",
            note_count=int(status.get("current_note_count", 0) or 0),
            pending_uploads=self.spool.pending_count(),
            uptime_seconds=int(status.get("uptime_seconds", 0) or 0),
            version=platform.node()[:40],
        )
        async with self._client_factory() as client:
            try:
                response = await client.post("/api/ingest/heartbeat",
                                             json=beat.model_dump())
            except httpx.HTTPError as exc:
                self._note_failure(f"Cannot reach the server: {exc.__class__.__name__}")
                return False
        if response.status_code == 200:
            self.state.reachable = True
            self.state.authenticated = True
            self.state.last_contact_at = time.time()
            self.state.last_error = ""
            return True
        if response.status_code in (401, 403):
            self.state.reachable = True
            self.state.authenticated = False
            self.state.last_error = "The server rejected this client's secret."
            return False
        self._note_failure(f"Server answered {response.status_code}")
        return False

    async def hello(self) -> dict:
        """One-shot check for the settings page's 'Test connection' button."""
        if not self.settings.server_url:
            return {"ok": False, "error": "No server address set."}
        if not self.settings.client_secret:
            return {"ok": False, "error": "No client secret set."}
        async with self._client_factory() as client:
            try:
                response = await client.post("/api/ingest/hello")
            except httpx.HTTPError as exc:
                self.state.reachable = False
                return {"ok": False, "error": f"Could not reach the server ({exc.__class__.__name__})."}
        if response.status_code == 200:
            self.state.reachable = True
            self.state.authenticated = True
            self.state.last_error = ""
            self.state.last_contact_at = time.time()
            body = response.json()
            return {"ok": True, "client_name": body.get("client_name", ""),
                    "client_id": body.get("client_id", "")}
        if response.status_code in (401, 403):
            self.state.reachable = True
            self.state.authenticated = False
            return {"ok": False, "error": "The server did not accept that secret."}
        return {"ok": False, "error": f"The server answered {response.status_code}."}

    def _note_failure(self, message: str) -> None:
        self.state.reachable = False
        self.state.authenticated = False
        self.state.last_error = message
        log.warning("%s", message)
