"""Entry point: `python -m midi_memory.server` or the `midi-memory-server` script."""
from __future__ import annotations

import logging

import uvicorn

from midi_memory.server.config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    uvicorn.run(
        "midi_memory.server.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
