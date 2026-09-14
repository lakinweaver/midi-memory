"""Live USB MIDI capture on Linux via the ALSA sequencer.

Uses python-alsa-midi: pure Python (cffi), no compilation, and supported on
CPython 3.9 through 3.14, so it never pins the project to an old interpreter.
"""
from __future__ import annotations

import asyncio
import logging

from alsa_midi import (  # noqa: F401  (import proves the backend is usable)
    WRITE_PORT,
    AsyncSequencerClient,
    MidiBytesEvent,
    PortExitEvent,
    PortStartEvent,
)

from midi_memory.client.midi.source import MidiSource

log = logging.getLogger(__name__)

CLIENT_NAME = "midi-memory"
RECONCILE_SECONDS = 5.0
_SKIP_PORTS = ("midi through", "midi-memory", "rtpmidi")


class AlsaSource(MidiSource):
    kind = "alsa"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: AsyncSequencerClient | None = None
        self._port = None
        self._subscribed: set[tuple[int, int]] = set()

    async def run(self) -> None:
        while not self._closing:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ALSA MIDI capture failed; retrying in 5s")
                self.set_status(False, "")
                await asyncio.sleep(5)

    async def _run_once(self) -> None:
        self._client = AsyncSequencerClient(CLIENT_NAME)
        self._port = self._client.create_port("capture", WRITE_PORT)
        self._subscribed.clear()

        # Announcements tell us the moment a keyboard is switched on or unplugged.
        try:
            self._client.subscribe_port((0, 1), self._port)  # System:Announce
        except Exception:
            log.warning("Could not subscribe to ALSA announcements; relying on polling")

        await self._reconcile()
        reconcile_task = asyncio.create_task(self._reconcile_loop())
        try:
            while not self._closing:
                event = await self._client.event_input(prefer_bytes=True, timeout=1.0)
                if event is None:
                    continue
                if isinstance(event, MidiBytesEvent):
                    self.emit_bytes(event.midi_bytes)
                elif isinstance(event, (PortStartEvent, PortExitEvent)):
                    await self._reconcile()
        finally:
            reconcile_task.cancel()
            await self._teardown()

    async def _reconcile_loop(self) -> None:
        """Belt-and-braces poll in case an announcement is ever missed."""
        while not self._closing:
            await asyncio.sleep(RECONCILE_SECONDS)
            try:
                await self._reconcile()
            except Exception:
                log.exception("ALSA port reconcile failed")

    async def _reconcile(self) -> None:
        """Connect to every matching keyboard port, and notice when they vanish."""
        assert self._client is not None and self._port is not None
        ports = self._client.list_ports(input=True, include_midi_through=False)
        live: set[tuple[int, int]] = set()
        names: list[str] = []

        for info in ports:
            name = f"{info.client_name}: {info.name}" if info.client_name else info.name
            if any(skip in name.lower() for skip in _SKIP_PORTS):
                continue
            if not self.matches(name):
                continue
            address = (info.client_id, info.port_id)
            live.add(address)
            names.append(name)
            if address not in self._subscribed:
                try:
                    self._port.connect_from(address)
                    self._subscribed.add(address)
                    log.info("Connected to MIDI port %s at %s", name, address)
                except Exception:
                    log.exception("Could not connect to MIDI port %s", name)

        for address in self._subscribed - live:
            self._subscribed.discard(address)
            log.info("MIDI port at %s went away", address)

        self.set_status(bool(self._subscribed), ", ".join(sorted(names)))

    async def _teardown(self) -> None:
        self.set_status(False, "")
        client, self._client, self._port = self._client, None, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    async def aclose(self) -> None:
        await super().aclose()
        await self._teardown()


def list_ports() -> list[str]:
    """Names of every ALSA MIDI input port. Used by `python -m midi_memory.tools.ports`."""
    from alsa_midi import SequencerClient

    client = SequencerClient("midi-memory-probe")
    try:
        return [
            f"{p.client_name}: {p.name}  [{p.client_id}:{p.port_id}]"
            for p in client.list_ports(input=True)
        ]
    finally:
        client.close()
