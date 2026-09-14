# MIDI Memory

An always-on notepad for the ideas you play and forget.

As a musician and composer, I have spent a good chunk of my life trying to remember the melody I just played or the chords I just came up with minutes ago at the piano. There are several devices on the market now that allow you to record the MIDI output from your keyboard to provide a running "memory" of the ideas you create at the keyboard. These are awesome, but I wanted to create a similar system that allowed me to capture my ideas and store them on my home server. This project is the result!

Using a lightweight Raspberry Pi client, anything you play on a MIDI keyboard is captured, and when you stop for a while the take is filed as its
own **session** and sent to a server on your network, where you browse, search, tag,
replay and download it. The client records continuously and catalogs all MIDI data it receives.

It comes in two parts:

| | Runs on | Job |
| --- | --- | --- |
| **Server** | Anything with Docker | The library: browse, search, play back, download. Registers clients and receives their recordings. |
| **Client** | A Pi beside the instrument | Records. Spools takes locally and uploads them. One settings page is its entire UI. |

Using this system, you can deploy multiple clients to as many keyboards as you want. Your ideas are sent to a central library server where they can be easily backed up. Clients continue recording even when they cannot access the server.

## What the client does

- **Records continuously** from a USB MIDI keyboard, with no interaction.
- **Splits takes automatically** after a configurable silence (default 45s).
- **Ignores accidental key brushes** — by default, takes under 4 notes or 2 seconds are discarded.
- **Survives power cuts** — every note is flushed to the client's disk as it is played, and an
  interrupted session is finalized on the next start.
- **Survives the server being down** — your ideas wait in a local spool and are uploaded to the server as soon as it is available.

## What the server does
- **Browse and search** your ideas ("sessions") by name, tag, free text, date range, length, starred status,
  and which client device it was recorded on.
- **Plays back ideas in the browser** with a piano roll and a sampled piano voice, with loop and speed control.
- **Allows downloads** of any session as a standard `.mid` file.

## Setting it up

### 1. The server

```bash
git clone https://github.com/lakinweaver/midi-memory/ midi-memory && cd midi-memory
docker compose up -d
```

Open `http://<that-machine>:8080`. To require a password, set `MIDI_MEMORY_PASSWORD`
before bringing it up; capture clients are unaffected either way, since they
authenticate with their own secret rather than with the login.

Recordings and the database land in `./data`. Back that up and you have backed up
everything that matters — the recordings are plain files you can copy out at any time.

#### Running on a NAS

The container writes to `/data`, which is your `./data` bind mount. On a NAS —
Synology, unRAID, QNAP — that directory belongs to a real user there, with ACLs on top,
and a container running as some invented uid cannot write to it. That is what `PUID` and
`PGID` are for.

`docker-compose.yml` defaults them to **1030** and **100**, which may not be correct for your setup. You can add the ids for your docker user in a `.env` beside `docker-compose.yml` (see `.env.example`),
or edit the compose file's `environment:` block, which is easier from Synology's
Container Manager:

```bash
id your-user        # e.g. uid=1030(docker) gid=100(users)
```

Set either of these to empty and the container should adopt whoever already owns `./data`,
which works without knowing any of this — it logs which ids it picked on startup.

**After pulling a new version, rebuild.** `docker compose up -d` on its own reuses the
image you already have, so changes to the Dockerfile or the entrypoint are not picked up:

```bash
docker compose up -d --build
```

### 2. Register a client

In the server's **Settings → Capture clients**, add a client and name it. It shows a
secret **once**; only its hash is stored, so it cannot be shown again. Losing it costs
one click on "New secret".

### 3. The client

```bash
git clone https://github.com/lakinweaver/midi-memory/ ~/midi-memory && cd ~/midi-memory
./scripts/install_client_pi.sh
```

The installer sets up the virtualenv, installs the system packages and ALSA bits,
creates `/var/lib/midi-memory-client`, generates a password for the client's own page,
and installs a systemd service so recording resumes on every boot. It is safe to re-run
to upgrade, and it removes the pre-split `midi-memory` service if it finds one.

Then open the address it prints — `http://<pi>:8081` — and paste in the server address
and the secret. That is the only configuration either half needs; everything else has a
working default.

**Run it as yourself, not with `sudo`.** The script calls `sudo` for the handful of
steps that need it (apt, systemd, `/var/lib`). Running the whole thing as root creates
the virtualenv and `.env` owned by root inside your home directory, and the service —
which runs as you — then cannot read them. If you already did this, the script detects
and repairs the ownership on the next run.

If the install fails partway — flaky Pi wifi timing out against apt or PyPI is the usual
cause — just re-run it. It is idempotent and resumes.

#### Client password details
The client's page has its own password, generated on the first install and printed once.
To find it again:

```bash
grep MIDI_MEMORY_PASSWORD ~/midi-memory/.env
```

Change it there if needed and `sudo systemctl restart midi-memory-client`.

### Upgrading an existing single-box install

The server keeps the old data directory layout exactly, so point it at your existing
`data/` and the whole library is there.

## Updating an install

### The server

```bash
git pull
docker compose up -d --build
```

**`--build` is not optional.** The compose file names the image, so `docker compose up -d`
on its own finds that image already present and reuses it — your pull changes nothing and
the container comes back exactly as it was. From Synology's Container Manager the
equivalent is Action → Build, then Start; the Start button alone has the same problem.

Your recordings are in `./data` and a rebuild does not touch them. Schema changes are
applied on startup, so there is no migration step.

### A capture client

The Pi's install is editable — the installed package *is* the git checkout — so for
changes to the code, templates or the settings page, a pull and a restart is the whole
update:

```bash
cd ~/midi-memory
git pull
sudo systemctl restart midi-memory-client
```

Restarting mid-take is safe: the service gets a SIGINT and twenty seconds to finalise the
recording in progress into the spool before it goes down, and anything already spooled
uploads when it comes back.

Re-run the installer instead when `pyproject.toml` changed, when the installer or the
systemd unit changed, or whenever you would rather not think about it:

```bash
cd ~/midi-memory
git pull
./scripts/install_client_pi.sh
```

It is idempotent: it keeps your `.env`, reinstalls dependencies, and restarts the service.

## Development, on any machine

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,server,client]'
.venv/bin/python scripts/fetch_samples.py     # optional: piano samples (or use the UI)
cp .env.server.example .env.server
cp .env.client.example .env.client
```

Then run the two halves in separate terminals:

```bash
.venv/bin/python -m midi_memory.server    # http://localhost:8080
.venv/bin/python -m midi_memory.client    # http://localhost:8081
```

They share a directory here, which they never do in production, so each also reads its
own `.env.server` / `.env.client` on top of the shared `.env`. Later files win.

With no MIDI hardware the client runs fine and records nothing. To see the whole thing
work end to end, set `MIDI_MEMORY_MIDI_SOURCE=mock` in `.env.client` and it plays itself.
To fill the library without a client at all:

```bash
.venv/bin/python -m midi_memory.tools.seed --count 14
```

```bash
.venv/bin/python -m pytest                        # 156 tests
.venv/bin/python -m midi_memory.tools.ports       # what MIDI ports can this machine see?
```

## Configuration

Everything is set through environment variables or `.env` — see `.env.server.example`
and `.env.client.example`. All live variables can be placed in `.env`.

**Server**

| Setting | Default | What it does |
| --- | --- | --- |
| `MIDI_MEMORY_PASSWORD` | *(empty)* | Password for the library. Empty disables the login. |
| `MIDI_MEMORY_DATA_DIR` | `data` | Where recordings and the database live. |
| `MIDI_MEMORY_PORT` | `8080` | HTTP port. |
| `PUID` / `PGID` | `1030` / `100` | Docker only: who the server runs as. Empty adopts the owner of the mounted directory. |

**Client**

| Setting | Default | What it does |
| --- | --- | --- |
| `MIDI_MEMORY_SERVER_URL` | *(empty)* | Base URL of the server. Settable from the client's page. |
| `MIDI_MEMORY_CLIENT_SECRET` | *(empty)* | Issued by the server. Settable from the client's page. |
| `MIDI_MEMORY_IDLE_SECONDS` | `45` | Silence that ends a session. |
| `MIDI_MEMORY_MIN_NOTES` | `4` | Fewer notes than this is treated as an accident. |
| `MIDI_MEMORY_MIN_SECONDS` | `2` | Shorter than this is treated as an accident. |
| `MIDI_MEMORY_DEVICE_MATCH` | *(empty)* | Record only from ports whose name contains this. |
| `MIDI_MEMORY_MIDI_SOURCE` | `auto` | `auto`, `alsa`, `portable`, `mock` or `none`. |
| `MIDI_MEMORY_KEEP_UPLOADED_DAYS` | `7` | Days to keep a local copy after the server confirms it. |
| `MIDI_MEMORY_PORT` | `8081` | HTTP port for the settings page. |

The recording settings are also editable from the client's page, along with the server
address and secret. Those changes take effect immediately — on the next recorded note,
with no restart — and are saved to `settings.json` in the client's data directory, which
is layered on top of `.env` at startup.

**Tuning the idle timeout.** 45 seconds suits most people: long enough to think between
phrases, short enough that unrelated ideas do not end up in the same file. If your takes
keep getting merged, lower it; if one idea keeps getting split in half, raise it.

## How it works

```
client:  USB keyboard → MidiSource → asyncio queue → Recorder
                                                       ↓ events.jsonl, flushed per note
                                                     idle timeout
                                                       ↓
                                            spool/<id>/ (session.mid + upload.json)
                                                       ↓ uploader, bearer secret, retries
server:  POST /api/ingest/sessions → sessions/<id>/ + SQLite row → library UI (SSE)
```

Each session is a directory, the same shape on both sides:

- `events.jsonl` — the source of truth, appended and flushed as you play.
- `session.mid` — a standard type-0 MIDI file, rendered when the session closes.
- `upload.json` — the metadata the server files it under. Written last, which is what
  marks the take as finished and ready to send.

The client computes every statistic — duration, note range, average velocity, the pitch
strip the library draws — because it has just parsed the MIDI to render the file anyway.
The server stores what it is given. Metadata (names, tags, notes, stars) lives in SQLite
on the server; the recordings themselves stay plain files.

### The spool

A take leaves the client only once the server has said, in so many words, that it has
it. If the server is down the spool simply grows, and the client's page says how many
takes are waiting; when it comes back they go up oldest first, so an evening's work
arrives in the order it was played.

An upload whose acknowledgement went missing is answered with `duplicate` rather than
filed twice, so a retry is always safe. After a successful upload the local copy is kept
for `KEEP_UPLOADED_DAYS` — a few megabytes of insurance against a mistake at the other
end.

### Client secrets

A client secret is 256 bits from the system CSPRNG, and only its SHA-256 hash is stored.
That is deliberately a fast hash rather than bcrypt: there is no dictionary to run
against a random token, nothing for a slow KDF to buy, and it lets the hash be an indexed
column so authenticating an upload is one lookup. Revoking a client stops its uploads
without disturbing the recordings it already made; deleting one is only allowed when it
has none.

### MIDI backends

| Platform | Package | Notes |
| --- | --- | --- |
| Linux / Raspberry Pi | [`python-alsa-midi`](https://github.com/Jajcus/python-alsa-midi) | Default. Pure Python, supports CPython 3.9–3.14, uses the native ALSA sequencer and its port-announce events for hotplug. |
| macOS / Windows | [`rtmidi2`](https://github.com/gesellkammer/rtmidi2) | Optional (`pip install -e '.[portable]'`), for live capture while developing. Needs CPython ≤ 3.13. |

`python-rtmidi` is deliberately **not** used: its last release was November 2023 and ships
no wheels past CPython 3.12, which would have pinned the whole project to an ageing
interpreter.

All events are timestamped with `time.monotonic()` when received. On a Pi the
USB-to-userspace jitter is well under a millisecond — far finer than matters here — and
a single clock keeps recording, replay and crash recovery consistent.

### Version

`midi_memory/__init__.py` holds `__version__`, and it is the only place a version
number is written down. `pyproject.toml` reads it from there, both apps report it as
their FastAPI version (so a client sees it in the handshake), and both UIs show it in
their footer. Releasing is one edit.

### The icon

`brand/icon.svg` is the source: a placeholder mark, the app's ink ground with one amber
record lamp. Everything under `midi_memory/*/static/brand/` is generated from it and
should not be edited by hand. Replace it with a real logo and run

```bash
./scripts/render_icons.sh
```

which copies it and renders 512/180/32/16px PNGs into both apps' static directories. Those
files are committed, so neither the Docker build nor the Pi install needs ImageMagick —
only whoever changes the logo does. Pages link the SVG first and the PNGs as fallbacks,
stamped with the file's mtime, so a new logo replaces a cached one rather than sitting
behind it.

### Piano samples

Playback uses a recorded piano rather than a synthesised tone. The samples are about
2 MB and are deliberately not in the repository. The Docker image bakes them in at build
time, so a running server never depends on the internet; the Settings dialog has a
Download button as a fallback, and `scripts/fetch_samples.py` does the same from the
command line. Either is safe to re-run — files already present are skipped.

Capture clients do not get them at all. They have no player, so that is two megabytes
and one flaky-network step the Pi install does not need.

## Security

This is built for your own network: one password on the library, a signed cookie, a
per-client bearer secret, and plain HTTP. That is proportionate to a home network — but it
is **not** enough to expose to the internet. If you need that, put it behind a reverse
proxy with TLS and real authentication.

## Layout

```
midi_memory/
  shared/    protocol.py   the contract between the halves: upload, heartbeat, layout
             auth.py       password + signed cookie
             config.py     the settings both sides have
             midi/         events.py (normalised event model), smf.py (MIDI file IO)
  server/    main.py       FastAPI app and pages
             db.py         SQLite + search          clients.py  registry, secrets, status
             events.py     SSE pub/sub              samples.py  the sampled piano
             api/          sessions, tags, settings, status, clients, ingest
  client/    main.py       the settings page        capture.py  source → recorder → spool
             spool.py      the local queue          uploader.py delivery, retries, heartbeat
             settings_store.py                      midi/       recorder + backends
  tools/     ports.py (client-side), seed.py (server-side)
```

The test suite mirrors it — `tests/shared`, `tests/server`, `tests/client` — and drives
the recorder with a fake clock, so session splitting, held-note suppression and crash
recovery are verified without waiting in real time or needing a keyboard. The uploader
tests run a real client against the real server app over httpx's ASGI transport, so the
join between the two halves is tested with only the socket replaced.

## Disclosure and Credits 

This project is 90% "vibe-coded" with Claude Code. My feelings on AI are mixed, but the ability to _quickly_ deploy a problem-solving app like this one is too useful for me to discount. While I am familiar with the web stack I asked Claude to use and have written similar projects by hand in the past, I have made no effort to audit the code. **This app simply fills a need for me, and I'm making the repo public in case it does for you, too.**

Piano samples are the [Salamander Grand Piano](https://archive.org/details/SalamanderGrandPianoV3)
by Alexander Holm, licensed **CC BY 3.0**, as redistributed by the Tone.js project.
Typefaces are [Fraunces](https://fonts.google.com/specimen/Fraunces) and
[DM Mono](https://fonts.google.com/specimen/DM+Mono), both SIL Open Font License,
served locally so the app works with no internet connection.
