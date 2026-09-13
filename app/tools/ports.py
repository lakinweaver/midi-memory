"""List the MIDI input ports this machine can see: `python -m app.tools.ports`."""
from __future__ import annotations

import platform
import sys


def main() -> int:
    backends = []
    if platform.system() == "Linux":
        backends.append(("ALSA sequencer", "app.midi.alsa_source"))
    backends.append(("rtmidi2", "app.midi.portable_source"))

    found = False
    for label, module_name in backends:
        try:
            module = __import__(module_name, fromlist=["list_ports"])
        except ImportError as exc:
            print(f"{label}: unavailable ({exc})")
            continue
        try:
            ports = module.list_ports()
        except Exception as exc:
            print(f"{label}: error listing ports ({exc})")
            continue
        found = True
        print(f"{label}: {len(ports)} input port(s)")
        for port in ports:
            print(f"  - {port}")

    if not found:
        print("\nNo MIDI backend is installed for this platform.")
        print("  Raspberry Pi / Linux:  pip install '.[alsa]'")
        print("  macOS / Windows:       pip install '.[portable]'   (needs CPython <= 3.13)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
