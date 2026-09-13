"""Diagnose MIDI input and output on this machine.

    python -m app.tools.midi_check            # report what is connected
    python -m app.tools.midi_check --play     # also send a test scale

Exercises the same AlsaSink the app uses, so whatever it reports is what the
app sees -- not a separate reimplementation that might behave differently.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys

from app.config import get_settings
from app.midi.events import MidiEvent

TEST_NOTES = [60, 62, 64, 65, 67]


def _heading(text: str) -> None:
    print(f"\n\033[1;33m== {text}\033[0m")


def _dump_alsa_ports() -> bool:
    """Every sequencer port ALSA can see, and what it can do."""
    try:
        from alsa_midi import SequencerClient
    except ImportError as exc:
        print(f"  alsa-midi is not installed ({exc})")
        print("  Install it with:  .venv/bin/pip install -e '.[alsa]'")
        return False

    client = SequencerClient("midi-memory-check")
    try:
        inputs = client.list_ports(input=True)
        outputs = client.list_ports(output=True)

        _heading("Ports that can SEND to us (your keyboard)")
        if not inputs:
            print("  none found")
        for p in inputs:
            print(f"  {p.client_id}:{p.port_id}  {p.client_name}: {p.name}")

        _heading("Ports we can SEND TO (instrument MIDI in)")
        if not outputs:
            print("  none found")
            print("  If your piano is connected but absent here, its USB port may be")
            print("  receive-only, or the instrument may need MIDI IN enabled in its menu.")
        for p in outputs:
            print(f"  {p.client_id}:{p.port_id}  {p.client_name}: {p.name}")
        return bool(outputs)
    finally:
        client.close()


async def _check_sink(play: bool) -> int:
    settings = get_settings()
    _heading("What the app's output backend selects")

    try:
        from app.midi.alsa_sink import AlsaSink
    except ImportError as exc:
        print(f"  Cannot load the ALSA output backend: {exc}")
        return 1

    sink = AlsaSink(settings)
    if settings.device_match:
        print(f"  MIDI_MEMORY_DEVICE_MATCH is set to {settings.device_match!r};")
        print("  only ports whose name contains that will be used.")

    await sink.open()
    for _ in range(12):                       # give reconcile a moment to run
        await asyncio.sleep(0.5)
        if sink.connected:
            break

    if not sink.connected:
        print("  NOT CONNECTED to any instrument.")
        print("  Ports are skipped when their name contains any of: midi through,")
        print("  midi-memory, rtpmidi -- or when DEVICE_MATCH does not match.")
        await sink.aclose()
        return 1

    print(f"  connected to: {sink.port_name}")

    if play:
        _heading("Sending a test scale")
        print("  Listen to the instrument now...")
        for note in TEST_NOTES:
            await sink.send(MidiEvent(0.0, 0x90, note, 100))
            await asyncio.sleep(0.35)
            await sink.send(MidiEvent(0.0, 0x80, note, 0))
        await sink.panic()
        print("  Sent 5 notes (C D E F G).")
        print("  If you heard nothing, the connection is fine but the instrument")
        print("  is ignoring incoming MIDI -- check its Local Control / MIDI IN")
        print("  setting, and that it is not muted or set to a silent voice.")

    await sink.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--play", action="store_true",
                        help="send a short test scale to the instrument")
    parser.add_argument("--verbose", action="store_true", help="show backend logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="  [%(levelname)s] %(name)s: %(message)s",
    )

    if platform.system() != "Linux":
        print("This check targets Linux (the Raspberry Pi). "
              "On macOS there is no ALSA backend.")
        return 1

    print(f"MIDI Memory diagnostic  --  python {sys.version.split()[0]}")
    _dump_alsa_ports()
    return asyncio.run(_check_sink(args.play))


if __name__ == "__main__":
    sys.exit(main())
