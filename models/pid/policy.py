"""PIDAgent - the rule-based baseline (spec section 45).

A model that can actually drive the route, so the evaluation engine has
something to measure other than a car leaving the road. It is the reference the
learned models are compared against, and the expert that generates their
training data.

The controller itself now lives in `simulator/lowlevel.py`, because spec section
14 puts it there: a high-level policy emits a manoeuvre and a conventional
controller executes it. This agent is a thin wrapper that drives that controller
at full cruise - which makes it exactly "the VLM policy with the decision head
removed", and therefore the right thing to compare against.

It reads no pixels. It is given the route and the lead vehicle as ground truth
(both declared in `required_sensors`), exactly as expert autopilots are in the
CARLA literature. That is the point of a baseline: it establishes what the
scenario looks like when it is driven competently, not how hard the perception
problem is.
"""

from __future__ import annotations

from typing import Any

from simulator.lowlevel import LaneKeepingController
from simulator.policy import DrivingPolicy
from simulator.types import Observation, VehicleControlAction


class PIDAgent(DrivingPolicy):
    name = "pid"
    required_sensors = ("route", "lead_vehicle", "speed")

    def __init__(self, target_speed_mps: float = 15.0, **controller_kwargs: Any):
        self.controller = LaneKeepingController(
            target_speed_mps=target_speed_mps, **controller_kwargs
        )

    def reset(self, config: dict[str, Any]) -> None:
        self.controller.reset(
            fixed_delta_seconds=config.get("fixed_delta_seconds") or 0.05,
            target_speed_mps=config.get("target_speed_mps"),
        )

    def infer(self, observation: Observation) -> VehicleControlAction:
        return self.controller.control(observation)
