"""Lane-following controller for scenario vehicles.

Scenario NPCs cannot use CARLA's traffic manager: the whole point of a
scenario is that the NPC does something specific at a specific moment, which
autopilot will not do. So they get a small explicit controller instead.

Pure pursuit for steering, proportional control on speed. It is not a good
driver and does not need to be - it needs to be a *repeatable* one.
"""

from __future__ import annotations

import math
from typing import Optional

import carla


def _yaw_error(target: carla.Location, vehicle: carla.Actor) -> float:
    """Angle from the vehicle's heading to the target, in radians."""
    tf = vehicle.get_transform()
    dx = target.x - tf.location.x
    dy = target.y - tf.location.y
    desired = math.atan2(dy, dx)
    current = math.radians(tf.rotation.yaw)
    return math.atan2(math.sin(desired - current), math.cos(desired - current))


def speed_of(vehicle: carla.Actor) -> float:
    v = vehicle.get_velocity()
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


class LaneFollower:
    """Drives an actor along its lane at a target speed.

    A lane change is expressed as a lateral blend: the steering target is
    interpolated between the vehicle's own lane and a target lane, so setting
    `blend` from 0 to 1 over a couple of seconds produces a smooth, physical
    lane change rather than a teleport.
    """

    def __init__(
        self,
        vehicle: carla.Vehicle,
        world_map: carla.Map,
        target_speed_mps: float,
        steer_gain: float = 0.6,
        speed_gain: float = 0.5,
    ) -> None:
        self.vehicle = vehicle
        self.map = world_map
        self.target_speed = target_speed_mps
        self.steer_gain = steer_gain
        self.speed_gain = speed_gain

        #: 0 = own lane, 1 = fully in `blend_target_side` lane.
        self.blend = 0.0
        self.blend_target_side: Optional[str] = None  # "left" | "right"
        #: False when a blend was requested but the target lane did not resolve.
        #: Without this the fallback below is silent, and a scenario reports a
        #: manoeuvre it never performed.
        self.blend_resolved = True

    def _lookahead_distance(self) -> float:
        # Longer lookahead at speed keeps the steering from oscillating.
        return min(max(speed_of(self.vehicle) * 1.2, 6.0), 18.0)

    def target_location(self) -> Optional[carla.Location]:
        here = self.map.get_waypoint(
            self.vehicle.get_location(),
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if here is None:
            return None

        ahead = here.next(self._lookahead_distance())
        if not ahead:
            return None
        own = ahead[0].transform.location

        if self.blend <= 0.0 or self.blend_target_side is None:
            return own

        neighbour = (
            ahead[0].get_left_lane()
            if self.blend_target_side == "left"
            else ahead[0].get_right_lane()
        )
        if neighbour is None or neighbour.lane_type != carla.LaneType.Driving:
            self.blend_resolved = False
            return own
        self.blend_resolved = True

        other = neighbour.transform.location
        t = min(max(self.blend, 0.0), 1.0)
        return carla.Location(
            x=own.x + (other.x - own.x) * t,
            y=own.y + (other.y - own.y) * t,
            z=own.z + (other.z - own.z) * t,
        )

    def step(self) -> carla.VehicleControl:
        target = self.target_location()
        if target is None:
            # Off the road network: coast to a stop rather than drive blind.
            return carla.VehicleControl(throttle=0.0, brake=0.5)

        steer = min(max(self.steer_gain * _yaw_error(target, self.vehicle), -1.0), 1.0)

        error = self.target_speed - speed_of(self.vehicle)
        if error >= 0:
            throttle, brake = min(self.speed_gain * error, 0.85), 0.0
        else:
            throttle, brake = 0.0, min(-self.speed_gain * error, 1.0)

        control = carla.VehicleControl(throttle=throttle, steer=steer, brake=brake)
        self.vehicle.apply_control(control)
        return control
