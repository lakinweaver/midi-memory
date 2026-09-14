"""A tiny in-process pub/sub bus driving the web UI's live updates over SSE."""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger(__name__)

SUBSCRIBER_BACKLOG = 64


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_BACKLOG)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, **payload: Any) -> None:
        """Fan out to every listener. A slow browser is dropped, never blocked."""
        message = {"type": event_type, **payload}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
