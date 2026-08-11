"""Ego vehicle spawning (spec section 19)."""

from __future__ import annotations

import math
from typing import Any

import carla

from simulator.types import Pose


def spawn_ego(
    world: carla.World,
    config: dict[str, Any],
    spawn_index: int = 0,
) -> tuple[carla.Vehicle, int]:
    """Spawn the ego vehicle at a deterministic spawn point.

    Returns the vehicle and the spawn index actually used. If the requested
    point is occupied the search walks forward, so a busy map degrades into a
    different-but-still-deterministic start rather than an exception.
    """
    blueprints = world.get_blueprint_library()
    bp = blueprints.find(config["blueprint"])
    bp.set_attribute("role_name", config.get("role_name", "ego"))

    spawn_points = world.get_map().get_spawn_points()
    if not spawn_points:
        raise RuntimeError("map exposes no spawn points")

    for offset in range(len(spawn_points)):
        index = (spawn_index + offset) % len(spawn_points)
        vehicle = world.try_spawn_actor(bp, spawn_points[index])
        if vehicle is not None:
            return vehicle, index

    raise RuntimeError("every spawn point on this map is occupied")


def pose_of(actor: carla.Actor) -> Pose:
    tf = actor.get_transform()
    return Pose(
        x=tf.location.x,
        y=tf.location.y,
        z=tf.location.z,
        roll=tf.rotation.roll,
        pitch=tf.rotation.pitch,
        yaw=tf.rotation.yaw,
    )


def speed_of(actor: carla.Actor) -> float:
    """Speed in m/s."""
    v = actor.get_velocity()
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def lateral_acceleration(actor: carla.Actor) -> float:
    """Acceleration across the vehicle's own axis, in m/s^2 (cornering load)."""
    a = actor.get_acceleration()
    right = actor.get_transform().get_right_vector()
    return a.x * right.x + a.y * right.y + a.z * right.z


def forward_speed(actor: carla.Actor, reference: carla.Actor) -> float:
    """Speed of `actor` along `reference`'s heading, in m/s.

    Used for closing speed: two cars in the same lane are only approaching
    each other along the direction of travel.
    """
    v = actor.get_velocity()
    forward = reference.get_transform().get_forward_vector()
    return v.x * forward.x + v.y * forward.y + v.z * forward.z


def longitudinal_acceleration(actor: carla.Actor) -> float:
    """Acceleration along the vehicle's own forward axis, in m/s^2.

    Signed: positive accelerating, negative braking. Projecting onto the
    forward vector keeps cornering out of the number, which matters once
    comfort metrics arrive.
    """
    a = actor.get_acceleration()
    forward = actor.get_transform().get_forward_vector()
    return a.x * forward.x + a.y * forward.y + a.z * forward.z
