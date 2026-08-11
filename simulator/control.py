"""Turning a planned trajectory into pedals and steering (spec section 13).

Most published driving models - TCP, Transfuser, InterFuser, LAV - emit
waypoints rather than control. This is the piece that lets them drive here:
the model says where to be, and the simulator works out how to get there with
*this* vehicle. The model never needs to know the wheelbase, the throttle map
or the timestep.

Waypoints arrive in the ego's frame at the moment of planning: forward is +x,
right is +y, in metres.
"""

from __future__ import annotations

import math

from simulator.types import TrajectoryAction, VehicleControlAction


class TrajectoryController:
    """Pure pursuit for steering, implied speed for the pedals."""

    def __init__(
        self,
        lookahead_m: float = 6.0,
        steer_gain: float = 1.1,
        throttle_per_mps: float = 0.035,
        speed_gain: float = 0.35,
        max_throttle: float = 0.75,
        brake_gain: float = 0.35,
        deadband_mps: float = 0.3,
    ) -> None:
        self.lookahead = lookahead_m
        self.steer_gain = steer_gain
        self.throttle_per_mps = throttle_per_mps
        self.speed_gain = speed_gain
        self.max_throttle = max_throttle
        self.brake_gain = brake_gain
        self.deadband = deadband_mps

    def target_speed(self, action: TrajectoryAction, dt: float) -> float:
        """Speed the trajectory implies.

        An explicit target_speed wins; otherwise it is the spacing of the
        waypoints over their horizon, which is what the model is really saying
        when it predicts where the car will be in two seconds.
        """
        points = action.waypoints
        if not points:
            return 0.0
        if points[0].target_speed_mps is not None:
            return float(points[0].target_speed_mps)

        last = points[-1]
        horizon = last.timestamp_s if last.timestamp_s else len(points) * dt
        if horizon <= 0:
            return 0.0
        return math.hypot(last.x, last.y) / horizon

    def control(
        self,
        action: TrajectoryAction,
        speed_mps: float,
        dt: float,
    ) -> VehicleControlAction:
        points = action.waypoints
        if not points:
            return VehicleControlAction(throttle=0.0, brake=0.5)

        # Aim at the first point beyond the lookahead, or the furthest there is.
        target = next(
            (p for p in points if math.hypot(p.x, p.y) >= self.lookahead),
            points[-1],
        )
        # Ego frame already, so the heading error is just the bearing to it.
        steer = max(-1.0, min(1.0, self.steer_gain * math.atan2(target.y, max(target.x, 0.1))))

        desired = self.target_speed(action, dt)
        error = desired - speed_mps
        feedforward = self.throttle_per_mps * desired
        command = feedforward + self.speed_gain * error

        if abs(error) < self.deadband and command >= 0:
            return VehicleControlAction(
                throttle=min(command, self.max_throttle), steer=steer, brake=0.0
            )
        if command >= 0:
            return VehicleControlAction(
                throttle=min(command, self.max_throttle), steer=steer, brake=0.0
            )
        return VehicleControlAction(
            throttle=0.0, steer=steer, brake=min(-command * self.brake_gain, 1.0)
        )
