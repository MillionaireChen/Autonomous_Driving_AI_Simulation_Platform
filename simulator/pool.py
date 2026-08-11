"""A pool of CARLA servers experiments are placed on (spec section 62).

An episode leases a server for its duration and returns it afterwards. With one
entry the platform behaves exactly as before and runs one episode at a time;
with two, two episodes run at once. Nothing else has to know which it is -
parallelism falls out of how many servers exist, rather than being a separate
code path that only gets exercised when someone remembers to test it.

Leasing is what makes this safe. Two episodes sharing one CARLA would interleave
their world ticks in synchronous mode and corrupt both, which is not an error
either of them would report - they would simply both be wrong.
"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class SimulatorEndpoint:
    name: str
    host: str
    port: int
    gpu: int = 0

    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port}, gpu {self.gpu})"


class NoSimulatorAvailable(RuntimeError):
    pass


class SimulatorPool:
    def __init__(self, endpoints: list[SimulatorEndpoint]) -> None:
        if not endpoints:
            raise ValueError("a simulator pool needs at least one endpoint")
        self.endpoints = list(endpoints)
        self._free: queue.Queue[SimulatorEndpoint] = queue.Queue()
        for endpoint in endpoints:
            self._free.put(endpoint)
        self._leased: set[str] = set()
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SimulatorPool":
        return cls([
            SimulatorEndpoint(
                name=entry.get("name", f"carla-{index}"),
                host=entry.get("host", "127.0.0.1"),
                port=int(entry["port"]),
                gpu=int(entry.get("gpu", 0)),
            )
            for index, entry in enumerate(config.get("simulators", []))
        ])

    @contextmanager
    def lease(self, timeout_s: float = 900.0) -> Iterator[SimulatorEndpoint]:
        """Take a server for the duration of the block, then hand it back.

        Blocks while every server is busy, which is the queue in spec section
        62: more experiments than simulators simply wait.
        """
        try:
            endpoint = self._free.get(timeout=timeout_s)
        except queue.Empty as exc:
            raise NoSimulatorAvailable(
                f"no simulator free within {timeout_s:.0f}s "
                f"({len(self.endpoints)} in the pool)"
            ) from exc

        with self._lock:
            self._leased.add(endpoint.name)
        try:
            yield endpoint
        finally:
            with self._lock:
                self._leased.discard(endpoint.name)
            self._free.put(endpoint)

    @property
    def size(self) -> int:
        return len(self.endpoints)

    @property
    def available(self) -> int:
        return self._free.qsize()

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            leased = set(self._leased)
        return [
            {"name": e.name, "host": e.host, "port": e.port, "gpu": e.gpu,
             "busy": e.name in leased}
            for e in self.endpoints
        ]
