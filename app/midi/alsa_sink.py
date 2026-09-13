"""MIDI output on Linux via the ALSA sequencer.

Mirrors alsa_source: one client, one port, reconnected whenever the instrument
appears or disappears.
"""
from __future__ import annotations

import asyncio
import logging

from alsa_midi import READ_PORT, AsyncSequencerClient, MidiBytesEvent

from app.midi.events import MidiEvent
from app.midi.sink import MidiSink

log = logging.getLogger(__name__)

CLIENT_NAME = "midi-memory-out"
RECONCILE_SECONDS = 5.0
_SKIP_PORTS = ("midi through", "midi-memory", "rtpmidi")


class AlsaSink(MidiSink):
    kind = "alsa"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: AsyncSequencerClient | None = None
        self._port = None
        self._connected_to: set[tuple[int, int]] = set()
        self._task: asyncio.Task | None = None

    async def open(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._maintain(), name="midi-sink")

    async def _maintain(self) -> None:
        while not self._closing:
            try:
                if self._client is None:
                    self._client = AsyncSequencerClient(CLIENT_NAME)
                    self._port = self._client.create_port("playback", READ_PORT)
                    self._connected_to.clear()
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ALSA MIDI output failed; retrying in 5s")
                await self._teardown()
            await asyncio.sleep(RECONCILE_SECONDS)

    async def _reconcile(self) -> None:
        """Connect our output port to every instrument that can receive MIDI."""
        assert self._client is not None and self._port is not None
        # output=True means "ports that accept events", i.e. instrument inputs.
        ports = self._client.list_ports(output=True, include_midi_through=False)
        live: set[tuple[int, int]] = set()
        names: list[str] = []
        fresh = False

        for info in ports:
            name = f"{info.client_name}: {info.name}" if info.client_name else info.name
            if any(skip in name.lower() for skip in _SKIP_PORTS):
                continue
            if not self.matches(name):
                continue
            address = (info.client_id, info.port_id)
            live.add(address)
            names.append(name)
            if address not in self._connected_to:
                try:
                    self._port.connect_to(address)
                    self._connected_to.add(address)
                    log.info("MIDI output connected to %s at %s", name, address)
                    fresh = True
                except Exception:
                    log.exception("Could not connect output to %s", name)

        for address in self._connected_to - live:
            self._connected_to.discard(address)
            log.info("MIDI output target at %s went away", address)

        self.set_status(bool(self._connected_to), ", ".join(sorted(names)))
        if fresh:
            # A previous run may have died mid-note; start from silence.
            await self.panic(force=True)

    async def _deliver(self, event: MidiEvent) -> None:
        client, port = self._client, self._port
        if client is None or port is None:
            return
        data = bytes(_message_bytes(event))
        try:
            # One message per event, so ALSA never has a partial byte sequence left over.
            await client.event_output_direct(MidiBytesEvent(data), port=port)
        except Exception:
            log.exception("Failed to send MIDI output")

    async def _teardown(self) -> None:
        self.set_status(False, "")
        client, self._client, self._port = self._client, None, None
        self._connected_to.clear()
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    async def aclose(self) -> None:
        await super().aclose()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._teardown()


def _message_bytes(event: MidiEvent) -> list[int]:
    """Two- or three-byte form of a channel-voice message."""
    if event.kind in (0xC0, 0xD0):  # program change / channel aftertouch
        return [event.status, event.data1 & 0x7F]
    return [event.status, event.data1 & 0x7F, event.data2 & 0x7F]
