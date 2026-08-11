"""PIDAgent - the rule-based baseline (spec section 45).

A model that can actually drive the route, so the evaluation engine has
something to measure other than a car leaving the road. It is the reference the
learned models are compared against, and the expert that generates their
training data.

Three parts, none of them clever:

* **Lateral**: pure pursuit onto the route it is given. Steering is the angle
  to a lookahead point that grows with speed, which is what keeps a controller
  from oscillating at speed and from cutting corners when slow.
* **Longitudinal**: the Intelligent Driver Model, which handles cruising and
  car-following with one continuous acceleration law.

It reads no pixels. It is given the route and the lead vehicle as ground truth
(both declared in `required_sensors`), exactly as expert autopilots are in the
CARLA literature. That is the point of a baseline: it establishes what the
scenario looks like when it is driven competently, not how hard the perception
problem is.
"""

from __future__ import annotations

import math
from typing import Any

from simulator.policy import DrivingPolicy
from simulator.types import Observation, VehicleControlAction


class PIDAgent(DrivingPolicy):
    name = "pid"
    required_sensors = ("route", "lead_vehicle", "speed")

    def __init__(
        self,
        target_speed_mps: float = 15.0,
        # Lateral
        steer_gain: float = 0.85,
        min_lookahead_m: float = 6.0,
        lookahead_time_s: float = 1.1,
        max_lookahead_m: float = 22.0,
        # Longitudinal (IDM)
        max_accel_mps2: float = 1.6,
        comfort_decel_mps2: float = 2.2,
        time_gap_s: float = 1.6,
        min_gap_m: float = 6.0,
        vehicle_length_m: float = 4.8,
        max_throttle: float = 0.75,
        throttle_scale: float = 3.0,
        throttle_per_mps: float = 0.035,
        brake_gain: float = 1.4,
        accel_deadband_mps2: float = 0.15,
    ) -> None:
        self.target_speed = target_speed_mps
        self.steer_gain = steer_gain
        self.min_lookahead = min_lookahead_m
        self.lookahead_time = lookahead_time_s
        self.max_lookahead = max_lookahead_m
        self.max_accel = max_accel_mps2
        self.comfort_decel = comfort_decel_mps2
        self.time_gap = time_gap_s
        self.min_gap = min_gap_m
        self.vehicle_length = vehicle_length_m
        self.max_throttle = max_throttle
        self.throttle_scale = throttle_scale
        self.throttle_per_mps = throttle_per_mps
        self.brake_gain = brake_gain
        self.accel_deadband = accel_deadband_mps2

        self._dt = 0.05

    # -- DrivingPolicy ----------------------------------------------------
    def reset(self, config: dict[str, Any]) -> None:
        self._dt = float(config.get("fixed_delta_seconds") or 0.05)
        if config.get("target_speed_mps"):
            self.target_speed = float(config["target_speed_mps"])

    def infer(self, observation: Observation) -> VehicleControlAction:
        throttle, brake = self._longitudinal_from_idm(observation)
        return VehicleControlAction(
            throttle=throttle, steer=self._steer(observation), brake=brake
        )

    # -- lateral ----------------------------------------------------------
    def _steer(self, obs: Observation) -> float:
        target = self._lookahead_point(obs)
        if target is None:
            return 0.0

        dx = target[0] - obs.ego_pose.x
        dy = target[1] - obs.ego_pose.y
        heading = math.radians(obs.ego_pose.yaw)
        # Angle to the target, wrapped into [-pi, pi].
        error = math.atan2(
            math.sin(math.atan2(dy, dx) - heading),
            math.cos(math.atan2(dy, dx) - heading),
        )
        return max(-1.0, min(1.0, self.steer_gain * error))

    def _lookahead_point(self, obs: Observation) -> tuple[float, float] | None:
        """First route point at least the lookahead distance away.

        Picking by distance rather than by index keeps the controller stable
        when the route's point spacing changes.
        """
        if not obs.route_waypoints:
            return None
        distance = min(
            max(obs.speed_mps * self.lookahead_time, self.min_lookahead),
            self.max_lookahead,
        )
        for point in obs.route_waypoints:
            if math.dist((obs.ego_pose.x, obs.ego_pose.y), point) >= distance:
                return point
        return obs.route_waypoints[-1]

    # -- longitudinal -----------------------------------------------------
    def _longitudinal_from_idm(self, obs: Observation) -> tuple[float, float]:
        """Intelligent Driver Model, mapped onto throttle and brake.

        Hand-rolled gap logic was tried first and chattered: it either had a
        hard threshold, which produced full-brake / full-throttle cycles, or a
        soft one, which crept closer and braked repeatedly. Measured 5 and 7
        hard braking events respectively on the same episode.

        IDM is the standard car-following law and has neither problem. A single
        continuous acceleration accounts for the speed error and the gap at
        once, so approaching a slower vehicle is one smooth deceleration into a
        steady following distance.

            a = a_max [ 1 - (v/v0)^4 - (s*/s)^2 ]
            s* = s0 + max(0, v·T + v·dv / (2 sqrt(a_max·b)))
        """
        v = obs.speed_mps
        speed_term = (v / self.target_speed) ** 4 if self.target_speed > 0 else 0.0
        gap_term = 0.0

        lead = obs.lead_vehicle
        if lead is not None:
            # Bumper to bumper, not centre to centre.
            s = max(lead.gap_m - self.vehicle_length, 0.1)
            dv = v - lead.speed_mps
            desired_gap = self.min_gap + max(
                0.0,
                v * self.time_gap
                + (v * dv) / (2.0 * math.sqrt(self.max_accel * self.comfort_decel)),
            )
            gap_term = (desired_gap / s) ** 2

        accel = self.max_accel * (1.0 - speed_term - gap_term)

        # Throttle is not acceleration. Holding a speed takes throttle just to
        # balance drag, so IDM's requested acceleration is a correction on top
        # of a feedforward term rather than the whole command. Without it the
        # car plateaued at 10 m/s against a 15 m/s target, because the throttle
        # implied by a small acceleration is not enough to hold speed.
        #
        # The coefficient is measured, not guessed: an earlier run held
        # 14.4 m/s at throttle 0.51, which is 0.035 per m/s.
        feedforward = self.throttle_per_mps * v
        command = feedforward + accel / self.throttle_scale

        # One continuous command through zero, rather than throttle-or-brake.
        # Dropping the throttle straight to zero at 15 m/s decelerates this
        # vehicle at about 5 m/s^2 on engine braking alone - which the score
        # counts as a hard brake even though the brake pedal is barely touched
        # (measured: brake 0.03, deceleration -5.10). Easing the throttle down
        # first means gentle slowing costs no throttle-lift spike, and the
        # brake is only used once there is no throttle left to give up.
        if command >= 0.0:
            return min(command, self.max_throttle), 0.0
        return 0.0, min(-command * self.brake_gain, 1.0)
