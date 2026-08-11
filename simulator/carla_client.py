"""CARLA connection and world lifecycle.

Synchronous fixed-timestep mode is mandatory (spec sections 17/18): the model
and the simulator must never free-run against each other, or two experiments
cannot be compared fairly.

Synchronous mode is also sticky. If a client exits without restoring it, the
server stays synchronous and the next client appears to hang. Every entry
point therefore goes through the context manager here, which restores the
original settings even when the episode raises.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import carla


def connect(host: str, port: int, timeout_seconds: float) -> carla.Client:
    client = carla.Client(host, port)
    client.set_timeout(timeout_seconds)
    # Fails fast with a clear error if nothing is listening.
    client.get_server_version()
    return client


@contextmanager
def synchronous_world(
    client: carla.Client,
    town: str,
    fixed_delta_seconds: float,
    weather: dict[str, Any] | None = None,
    reload: bool = True,
) -> Iterator[carla.World]:
    """Yield a world in synchronous fixed-timestep mode, then restore it."""
    if reload:
        world = client.load_world(town)
    else:
        world = client.get_world()

    if weather:
        world.set_weather(carla.WeatherParameters(**weather))

    original = world.get_settings()
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = fixed_delta_seconds
    world.apply_settings(settings)

    try:
        yield world
    finally:
        world.apply_settings(original)


def destroy_actors(actors: list[carla.Actor]) -> None:
    """Destroy actors, stopping sensors first so no callback fires mid-teardown."""
    for actor in actors:
        if actor is None:
            continue
        try:
            if actor.type_id.startswith("sensor.") and actor.is_listening:
                actor.stop()
            actor.destroy()
        except RuntimeError:
            # Already gone server-side; nothing to reclaim.
            pass
