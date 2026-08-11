"""Sensor attachment and frame collection.

In synchronous mode a sensor with its own sensor_tick does not necessarily
produce data on every world tick: a 10 Hz camera against a 20 Hz simulation
fires every other tick. So the queue is drained without blocking and the most
recent frame is carried forward, rather than blocking on get() and deadlocking
on the ticks that produce nothing.
"""

from __future__ import annotations

import queue
from typing import Any, Optional

import carla
import numpy as np


def to_rgb_array(image: carla.Image) -> np.ndarray:
    """CARLA delivers BGRA; return an (H, W, 3) RGB array."""
    buf = np.frombuffer(image.raw_data, dtype=np.uint8)
    buf = buf.reshape((image.height, image.width, 4))
    return buf[:, :, :3][:, :, ::-1]


class CameraSensor:
    """An RGB camera attached to a parent actor, configured from YAML."""

    def __init__(
        self,
        world: carla.World,
        parent: carla.Actor,
        config: dict[str, Any],
    ) -> None:
        blueprints = world.get_blueprint_library()
        bp = blueprints.find("sensor.camera.rgb")
        bp.set_attribute("image_size_x", str(config["width"]))
        bp.set_attribute("image_size_y", str(config["height"]))
        bp.set_attribute("fov", str(config["fov"]))
        bp.set_attribute("sensor_tick", str(config["sensor_tick"]))

        tf = config["transform"]
        transform = carla.Transform(
            carla.Location(x=float(tf["x"]), y=float(tf["y"]), z=float(tf["z"])),
            carla.Rotation(
                pitch=float(tf.get("pitch", 0.0)),
                yaw=float(tf.get("yaw", 0.0)),
                roll=float(tf.get("roll", 0.0)),
            ),
        )

        self.width = int(config["width"])
        self.height = int(config["height"])
        self._queue: queue.Queue = queue.Queue()
        self._latest: Optional[np.ndarray] = None
        self.frame_count = 0

        self.actor = world.spawn_actor(bp, transform, attach_to=parent)
        self.actor.listen(self._queue.put)

    def poll(self) -> Optional[np.ndarray]:
        """Drain whatever arrived and return the newest frame.

        Returns the previous frame unchanged on ticks where the camera did not
        fire, which is what a real 10 Hz sensor feeding a 20 Hz loop looks like.
        """
        while True:
            try:
                image = self._queue.get_nowait()
            except queue.Empty:
                break
            self.frame_count += 1
            self._latest = to_rgb_array(image)
        return self._latest

    def destroy(self) -> None:
        try:
            if self.actor.is_listening:
                self.actor.stop()
            self.actor.destroy()
        except RuntimeError:
            pass


class LaneInvasionSensor:
    """Records lane-marking crossings by the parent actor.

    CARLA fires one event per crossing, and a single event can list several
    markings at once (crossing a double line). Each event is counted once.
    """

    def __init__(self, world: carla.World, parent: carla.Actor) -> None:
        bp = world.get_blueprint_library().find("sensor.other.lane_invasion")
        self.actor = world.spawn_actor(bp, carla.Transform(), attach_to=parent)
        self.events: list[dict[str, Any]] = []
        self.actor.listen(self._on_event)

    def _on_event(self, event: carla.LaneInvasionEvent) -> None:
        self.events.append({
            "frame": event.frame,
            "crossed": sorted({str(m.type) for m in event.crossed_lane_markings}),
        })

    @property
    def count(self) -> int:
        return len(self.events)

    def destroy(self) -> None:
        try:
            if self.actor.is_listening:
                self.actor.stop()
            self.actor.destroy()
        except RuntimeError:
            pass


class CollisionSensor:
    """Records collision events on the parent actor.

    Collisions are latched rather than sampled: CARLA reports them as events,
    and missing one because no tick happened to observe it would make the
    termination condition unreliable.
    """

    def __init__(self, world: carla.World, parent: carla.Actor) -> None:
        bp = world.get_blueprint_library().find("sensor.other.collision")
        self.actor = world.spawn_actor(bp, carla.Transform(), attach_to=parent)
        self.events: list[dict[str, Any]] = []
        self.actor.listen(self._on_event)

    def _on_event(self, event: carla.CollisionEvent) -> None:
        impulse = event.normal_impulse
        self.events.append({
            "frame": event.frame,
            "other_actor": event.other_actor.type_id if event.other_actor else "unknown",
            "intensity": (impulse.x ** 2 + impulse.y ** 2 + impulse.z ** 2) ** 0.5,
        })

    @property
    def count(self) -> int:
        return len(self.events)

    def destroy(self) -> None:
        try:
            if self.actor.is_listening:
                self.actor.stop()
            self.actor.destroy()
        except RuntimeError:
            pass
