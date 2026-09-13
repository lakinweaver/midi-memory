# MIDI Memory

An always-on notepad for the ideas you play and forget.

Leave a Raspberry Pi connected to your digital piano and it listens continuously.
Anything you play is captured, and when you stop for a while the take is filed as its
own **session**. Later you browse, search, tag, replay and download them from a web
page on your home network. There is no record button — that is the whole point.

<!-- screenshots live in docs/ -->

## What it does

- **Records continuously** from a USB MIDI keyboard, with no interaction.
- **Splits takes automatically** after a configurable silence (default 45s).
- **Never cuts a held chord** — a session stays open while keys or the sustain pedal are down.
- **Ignores accidental key brushes** — takes under 4 notes or 2 seconds are discarded.
- **Survives power cuts** — every note is flushed to disk as it is played, and an
  interrupted session is finalised on the next start.
- **Browse and search** by name, tag, free text, date range, length and starred status.
- **Play back in the browser** with a piano roll, a sampled piano, loop and speed control.
- **Or play back on the piano itself**, with a toggle in the transport that sends the
  recording out to the attached instrument.
- **Download** any session as a standard `.mid` file.

## Quick start (development, on any machine)

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/fetch_samples.py     # optional: piano samples for playback
cp .env.example .env
.venv/bin/python -m app
```

Open <http://localhost:8080>. With no MIDI hardware present the app runs fine and simply
records nothing. To see it working end to end, set `MIDI_MEMORY_MIDI_SOURCE=mock` in
`.env` and it will play itself, or generate a library of demo takes:

```bash
.venv/bin/python -m app.tools.seed --count 14
```

## Install on a Raspberry Pi

```bash
git clone <your-repo-url> ~/midi-memory
cd ~/midi-memory
./scripts/install_pi.sh
```

The installer sets up the virtualenv, installs system packages, creates `/var/lib/midi-memory`,
generates a random password into `.env`, and installs and starts a systemd service so
recording resumes automatically on every boot. It is safe to re-run to upgrade.

**Run it as yourself, not with `sudo`.** The script calls `sudo` for the handful of
steps that need it (apt, systemd, `/var/lib`). Running the whole thing as root creates
the virtualenv and `.env` owned by root inside your home directory, and the service —
which runs as you — then cannot read them. If you already did this, the script detects
and repairs the ownership on the next run.

If the install fails partway — a flaky Pi wifi connection timing out against apt or
PyPI is the usual cause — just re-run it. The script is idempotent and resumes: packages
already installed are skipped, and samples already downloaded are not fetched again. To
skip the sample download entirely, `SKIP_SAMPLES=1 ./scripts/install_pi.sh`.

**Why a virtualenv on a single-purpose Pi?** Not to isolate from other apps — to isolate
from Debian's. Raspberry Pi OS marks the system Python as externally managed (PEP 668),
so `pip install` into it is refused without `--break-system-packages`, and overriding
that can break `apt`'s own Python tooling. The venv costs about 15 MB.

### "Permission denied" on .env

An earlier `sudo ./scripts/install_pi.sh` left root-owned files behind. Reclaim them and
re-run without sudo:

```bash
sudo chown -R $USER:$USER ~/midi-memory
cd ~/midi-memory && ./scripts/install_pi.sh
```

### If the web interface will not load

```bash
systemctl status midi-memory --no-pager     # is it running at all?
journalctl -u midi-memory -n 40 --no-pager  # why did it stop?
curl -sS localhost:8080/healthz             # does it answer locally?
ss -lntp | grep 8080                        # is it listening on all interfaces?
~/midi-memory/.venv/bin/python -c "import fastapi, uvicorn, mido, alsa_midi"
```

If `curl localhost` works but another machine cannot reach it, the app is fine and the
problem is the network path — check you are using the Pi's LAN address (`hostname -I`)
rather than `raspberrypi.local`, which needs mDNS working on the client.

```bash
journalctl -u midi-memory -f          # watch it work
sudo systemctl restart midi-memory    # after changing .env
.venv/bin/python -m app.tools.ports   # what MIDI ports can it see?
```

## Configuration

Everything is set through environment variables or `.env` — see `.env.example`.

The settings most worth tuning are also editable from the web UI, behind the cogwheel in
the header: idle timeout, the minimum-size thresholds, the device filter, and whether to
keep recording while playing to the instrument. Those changes take effect immediately —
on the next recorded note, with no restart — and are saved to `settings.json` in the data
directory, which is layered on top of `.env` at startup. Everything else (port, password,
data directory, which MIDI backend) is shown read-only there, since changing it needs a
restart. "Reset to .env" discards the overrides.

| Setting | Default | What it does |
| --- | --- | --- |
| `MIDI_MEMORY_PASSWORD` | *(empty)* | Shared password for the web UI. Empty disables login. |
| `MIDI_MEMORY_IDLE_SECONDS` | `45` | Silence that ends a session. |
| `MIDI_MEMORY_MIN_NOTES` | `4` | Fewer notes than this is treated as an accident. |
| `MIDI_MEMORY_MIN_SECONDS` | `2` | Shorter than this is treated as an accident. |
| `MIDI_MEMORY_DEVICE_MATCH` | *(empty)* | Record only from ports whose name contains this. |
| `MIDI_MEMORY_DATA_DIR` | `data` | Where recordings and the database live. |
| `MIDI_MEMORY_PORT` | `8080` | HTTP port. |
| `MIDI_MEMORY_MIDI_SOURCE` | `auto` | `auto`, `alsa`, `portable`, `mock` or `none`. |
| `MIDI_MEMORY_MIDI_SINK` | `auto` | Output backend for playing on the instrument. |
| `MIDI_MEMORY_CAPTURE_DURING_PLAYBACK` | `false` | Keep recording while playing to the instrument. |

**Tuning the idle timeout.** 45 seconds suits most people: long enough to think between
phrases, short enough that unrelated ideas do not end up in the same file. If your takes
keep getting merged, lower it; if one idea keeps getting split in half, raise it.

## How it works

```
USB keyboard → MidiSource → asyncio queue → Recorder → events.jsonl (flushed per note)
                                               ↓ idle timeout
                                         session.mid + SQLite row → web UI (SSE)
```

Each session is a directory under `$DATA_DIR/sessions/<id>/`:

- `events.jsonl` — the source of truth, appended and flushed as you play.
- `session.mid` — a standard type-0 MIDI file, rendered when the session closes.
- `meta.json` — start time and device, so crash recovery can rebuild the rest.

Metadata (names, tags, notes, stars) lives in SQLite; the recordings themselves are
plain files you can copy out at any time.

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

### Playing back on the piano

The transport has a **Browser / Piano** toggle. Browser playback renders audio locally,
so it works from any device on the network. Piano playback streams the recording out of
the Pi to the instrument, so it plays on real hammers.

Because the Pi holds the USB connection, piano playback is server-side and therefore
*shared*: there is one instrument, one transport, and every open tab sees and can stop
the same playback. The header shows an indicator whenever the piano is playing.

Two things this has to get right:

- **Recording is suppressed while the piano is playing.** Many digital pianos echo MIDI
  in straight back out of MIDI out. Without suppression, playing a session to the piano
  would be recorded as a new session, which would then be played back and recorded
  again. A short guard window after stopping catches echoes still in flight. If your
  instrument does not echo and you want to play along with a recording, set
  `MIDI_MEMORY_CAPTURE_DURING_PLAYBACK=true`.
- **Stopping always silences the instrument.** Cutting the stream mid-phrase would
  otherwise leave the piano sustaining until it is power-cycled, so stopping sends
  note-offs for everything held, lifts the pedal, then sends All Notes Off.

The toggle disables itself, with an explanation, when no instrument is connected, and
falls back to browser playback if the instrument disappears mid-session.

### Sustain pedal

Each note carries two lifetimes: how long the **key was held**, and how long it
**actually rang** once the pedal is accounted for. The piano roll draws the first, so a
heavily pedalled passage stays readable; playback uses the second, so it sounds like what
you played.

## Security

This is built for a device on your own network: one shared password, a signed cookie,
and plain HTTP. That is proportionate to a Pi behind a home router — but it is **not**
enough to expose to the internet. If you need that, put it behind a reverse proxy with
TLS and real authentication.

## Development

```bash
.venv/bin/python -m pytest        # 78 tests
.venv/bin/python -m pytest -q tests/test_recorder.py
```

The test suite drives the recorder with a fake clock, so session splitting, held-note
suppression and crash recovery are verified without waiting in real time or needing a
keyboard.

```
app/
  config.py      settings          db.py      SQLite + search
  service.py     wiring            auth.py    password + cookie
  events.py      SSE pub/sub       main.py    FastAPI app and pages
  midi/
    events.py    normalised event model, realtime-message filtering
    recorder.py  session segmentation, durability, crash recovery
    smf.py       MIDI file rendering, note extraction, stats
    source.py    input backend selection    alsa_source.py / portable_source.py / mock_source.py
    sink.py      output backend selection   alsa_sink.py / portable_sink.py
    device_player.py  transport for playing a session on the instrument
  api/           sessions, tags, playback, status + SSE
  static/, templates/
```

## Credits

Piano samples are the [Salamander Grand Piano](https://archive.org/details/SalamanderGrandPianoV3)
by Alexander Holm, licensed **CC BY 3.0**, as redistributed by the Tone.js project.
Typefaces are [Fraunces](https://fonts.google.com/specimen/Fraunces) and
[DM Mono](https://fonts.google.com/specimen/DM+Mono), both SIL Open Font License,
served locally so the app works with no internet connection.
