"""The seam: a real uploader talking to a real server, over a real HTTP client.

Nothing here is mocked except the socket. The server is the actual FastAPI app
running in-process, and httpx is pointed at it through ASGI transport -- so the
authentication, the multipart encoding, the status codes and the database write
are all the ones that will run on the Pi.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from midi_memory.client.midi.recorder import SessionRecord
from midi_memory.client.uploader import Uploader
from midi_memory.server.config import Settings as ServerSettings
from midi_memory.server.main import create_app
from midi_memory.shared.midi.events import MidiEvent
from midi_memory.shared.midi.smf import write_smf
from midi_memory.shared.protocol import MIDI_FILENAME


@pytest.fixture
def server(tmp_path):
    """The library server, running for real."""
    server_settings = ServerSettings(_env_file=None, data_dir=tmp_path / "server",
                                     password="")
    server_settings.ensure_dirs()
    with TestClient(create_app(server_settings)) as c:
        yield c


@pytest.fixture
def secret(server):
    return server.post("/api/clients", json={"name": "Upright"}).json()["secret"]


@pytest.fixture
def uploader(settings, spool, server, secret):
    """A client uploader wired to that server, using its real HTTP stack."""
    settings.server_url = "http://server.test"
    settings.client_secret = secret

    def factory():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://server.test",
            headers={"Authorization": f"Bearer {settings.client_secret}"},
        )

    return Uploader(settings, spool, client_factory=factory,
                    status_source=lambda: {"state": "idle", "connected": True,
                                           "port_name": "Yamaha", "uptime_seconds": 5})


def spool_a_session(settings, spool, session_id: str, notes: int = 6) -> None:
    """A finished take sitting in the spool, exactly as the recorder leaves one."""
    directory = settings.spool_dir / session_id
    directory.mkdir(parents=True, exist_ok=True)
    events = []
    for i in range(notes):
        events.append(MidiEvent(i * 0.5, 0x90, 60 + i, 80))
        events.append(MidiEvent(i * 0.5 + 0.4, 0x80, 60 + i, 0))
    write_smf(events, directory / MIDI_FILENAME, name=session_id)
    (directory / "events.jsonl").write_text('{"t":0,"s":144,"a":60,"b":80}\n')

    started = datetime.now(timezone.utc)
    spool.add(SessionRecord(
        id=session_id, started_at=started,
        ended_at=started + timedelta(seconds=3), duration_ms=3000,
        event_count=notes * 2, note_count=notes, lowest_note=60,
        highest_note=60 + notes - 1, avg_velocity=80.0,
        device_name="Yamaha P-125", directory=directory, fingerprint=[[0, 60, 8, 80]],
    ))


# -- the happy path ----------------------------------------------------------
async def test_a_spooled_take_reaches_the_library(settings, spool, server, uploader):
    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa", notes=7)

    assert await uploader.drain() == 1

    listed = server.get("/api/sessions").json()
    assert listed["total"] == 1
    session = listed["items"][0]
    assert session["id"] == "aaaaaaaaaaaaaaaa"
    assert session["note_count"] == 7
    assert session["client_name"] == "Upright"
    assert session["device_name"] == "Yamaha P-125"

    # And it is a real, playable recording on the far end.
    assert server.get(f"/api/sessions/{session['id']}/notes").json()["notes"]
    assert spool.pending() == [], "an acknowledged take leaves the queue"
    assert uploader.state.authenticated is True


async def test_the_event_log_travels_with_the_recording(settings, spool, server, uploader):
    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa")
    await uploader.drain()

    stored = server.app.state.settings.sessions_dir / "aaaaaaaaaaaaaaaa"
    assert (stored / "events.jsonl").exists(), "the source of truth goes up too"


async def test_takes_arrive_in_the_order_they_were_played(settings, spool, server, uploader):
    for session_id in ("first00000000000", "second0000000000", "third00000000000"):
        spool_a_session(settings, spool, session_id)
        time.sleep(0.01)

    assert await uploader.drain() == 3

    order = [s["id"] for s in server.get(
        "/api/sessions?sort=date&order=asc").json()["items"]]
    assert order[0] == "first00000000000"


async def test_a_heartbeat_tells_the_server_what_this_client_is_doing(
        settings, spool, server, uploader):
    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa")

    assert await uploader.heartbeat() is True

    listed = server.get("/api/status").json()["clients"]
    assert listed[0]["state"] == "idle"
    assert listed[0]["port_name"] == "Yamaha"
    assert listed[0]["pending_uploads"] == 1, "the server can see the backlog"


async def test_test_connection_names_the_client_back(uploader):
    result = await uploader.hello()
    assert result == {"ok": True, "client_name": "Upright",
                      "client_id": result["client_id"]}


# -- when things go wrong ----------------------------------------------------
async def test_an_unreachable_server_loses_nothing(settings, spool, uploader):
    """The whole point of the spool: the piano stays recorded when the server is not."""
    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa")

    def broken():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))),
            base_url="http://server.test",
        )

    uploader._client_factory = broken
    assert await uploader.drain() == 0

    assert spool.pending_count() == 1, "the take is still on disk, unharmed"
    assert uploader.state.reachable is False
    assert "Cannot reach the server" in uploader.state.last_error


async def test_the_backlog_drains_once_the_server_comes_back(settings, spool, server,
                                                             uploader):
    for session_id in ("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"):
        spool_a_session(settings, spool, session_id)
        time.sleep(0.01)

    def broken():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))),
            base_url="http://server.test",
        )

    working = uploader._client_factory
    uploader._client_factory = broken
    await uploader.drain()
    assert spool.pending_count() == 2

    uploader._client_factory = working
    assert await uploader.drain() == 2
    assert spool.pending() == []
    assert server.get("/api/sessions").json()["total"] == 2


async def test_a_rejected_secret_is_reported_rather_than_retried(settings, spool,
                                                                 server, uploader):
    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa")
    settings.client_secret = "wrong-secret"

    assert await uploader.drain() == 0
    assert uploader.state.reachable is True
    assert uploader.state.authenticated is False
    assert "rejected" in uploader.state.last_error
    assert spool.pending_count() == 1, "held, so a corrected secret still sends it"


async def test_a_lost_acknowledgement_does_not_file_the_take_twice(settings, spool,
                                                                   server, uploader):
    """The server has it, but the client never heard so and tries again."""
    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa")
    await uploader.drain()

    spool_a_session(settings, spool, "aaaaaaaaaaaaaaaa")   # back in the queue
    assert await uploader.drain() == 1, "the duplicate is accepted and retired"
    assert server.get("/api/sessions").json()["total"] == 1


async def test_an_id_another_client_already_used_is_re_filed(settings, spool,
                                                             server, uploader):
    """Vanishingly unlikely, but it must unblock the queue rather than wedge it."""
    other = server.post("/api/clients", json={"name": "Grand"}).json()["secret"]
    other_uploader = Uploader(
        settings.model_copy(update={"client_secret": other}), spool,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://server.test",
            headers={"Authorization": f"Bearer {other}"}),
    )
    spool_a_session(settings, spool, "cccccccccccccccc")
    await other_uploader.drain()

    # Now our client tries to use the same id for a different take.
    spool_a_session(settings, spool, "cccccccccccccccc")
    assert await uploader.drain() == 0, "the conflicting id is refused"

    pending = spool.pending()
    assert len(pending) == 1
    assert pending[0].id != "cccccccccccccccc", "re-filed under a fresh id"

    assert await uploader.drain() == 1
    assert server.get("/api/sessions").json()["total"] == 2
