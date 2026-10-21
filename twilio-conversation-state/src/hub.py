"""
Live fan-out to dashboard browsers.

Twilio talks to us over one WebSocket (/ws); dashboards subscribe over another
(/ui/ws). The Recorder writes each event to the store and then pushes it here,
so anything you see live is exactly what gets replayed later.
"""

import asyncio
import logging
from typing import Any

from src.store import CallStore

logger = logging.getLogger(__name__)


class LiveHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A stalled browser tab should never slow down a phone call.
                logger.warning("Dropping live update for a slow dashboard subscriber")


class Recorder:
    """Write an event to storage and broadcast it to dashboards in one call."""

    def __init__(self, store: CallStore, hub: LiveHub):
        self.store = store
        self.hub = hub

    def event(self, call_sid: str, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        event = self.store.append_event(call_sid, kind, data or {})
        self.hub.publish({"type": "event", "event": event})
        return event

    def call_changed(self, call_sid: str) -> None:
        call = self.store.get_call(call_sid)
        if call:
            self.hub.publish({"type": "call", "call": call})
