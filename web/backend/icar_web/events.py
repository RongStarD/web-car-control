from __future__ import annotations

import asyncio
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.latest: dict[str, dict[str, Any]] = {}

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", "event"))
        if event_type in {
            "map",
            "local_costmap",
            "global_costmap",
            "pose",
            "path",
            "scan",
            "telemetry",
            "navigation",
            "localization",
            "route",
            "bridge",
            "status",
            "health",
            "arbiter",
            "log",
            "command_error",
        }:
            self.latest[event_type] = event

        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
