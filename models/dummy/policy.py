"""DummyAgent - the first baseline (spec section 44).

It ignores every observation and always applies the same constant control.
That is the point: it proves the closed loop carries an action from a policy
into the vehicle. It is not expected to drive well, and from Phase 4 it exists
mainly as the negative control that crashes where a real model does not.
"""

from __future__ import annotations

from typing import Any

from simulator.policy import DrivingPolicy
from simulator.types import Observation, VehicleControlAction


class DummyAgent(DrivingPolicy):
    name = "dummy"
    required_sensors = ()

    def __init__(self, throttle: float = 0.4, steer: float = 0.0, brake: float = 0.0):
        self.throttle = throttle
        self.steer = steer
        self.brake = brake

    def reset(self, config: dict[str, Any]) -> None:
        return None

    def infer(self, observation: Observation) -> VehicleControlAction:
        return VehicleControlAction(
            throttle=self.throttle, steer=self.steer, brake=self.brake
        )
