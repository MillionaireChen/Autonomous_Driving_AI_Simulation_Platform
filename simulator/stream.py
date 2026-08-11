"""Live telemetry fan-out.

The simulation worker runs on its own thread and knows nothing about HTTP. It
publishes a plain dict per tick; whoever wants it - a WebSocket, a recorder, a
test - subscribes and reads.

Subscribers get bounded queues and are dropped from rather than blocked on: a
slow browser must never stall the simulation loop. Dropping frames is the
correct failure mode for a live view.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Optional


class Broadcaster:
    """One publisher, many subscribers, no back-pressure onto the publisher."""

    def __init__(self, maxsize: int = 120) -> None:
        self.maxsize = maxsize
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._closed = False
        #: Kept so a client joining mid-episode sees something immediately.
        self.latest: Optional[dict[str, Any]] = None

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self.maxsize)
        with self._lock:
            self._subscribers.append(q)
            if self._closed:
                q.put_nowait({"type": "end"})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, message: dict[str, Any]) -> None:
        self.latest = message
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(message)
            except queue.Full:
                # Drop the oldest and keep the newest: a live view wants the
                # present, not a backlog of the past.
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except (queue.Empty, queue.Full):
                    pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.publish({"type": "end"})

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
