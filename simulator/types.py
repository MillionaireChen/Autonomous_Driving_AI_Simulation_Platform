"""Data types exchanged between the simulator and a driving policy.

These are deliberately free of any CARLA import. A policy receives an
Observation and returns an action; it never touches the simulator API
(spec section 73). Keeping this module CARLA-free is what will later let a
model run in a separate process, container or machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class Pose:
    """Position in metres, orientation in degrees."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    def distance_to(self, other: "Pose") -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


@dataclass
class LeadVehicle:
    """Ground truth about the closest vehicle ahead, in the ego's lane.

    This is privileged information: a camera-only model has to infer it, and
    must not be handed it. It exists for rule-based experts and for generating
    training data, which is how expert autopilots work in the CARLA literature.
    A policy only receives it if it declares "lead_vehicle" in its
    required_sensors, so taking it is a visible, deliberate choice.
    """

    gap_m: float
    speed_mps: float


@dataclass
class Observation:
    """What the world looks like at one timestep (spec section 11).

    A policy declares which fields it needs; nothing requires every model to
    consume every sensor. Fields for sensors that are not mounted stay None.
    """

    frame_id: int
    timestamp: float
    speed_mps: float
    acceleration_mps2: float
    steering_angle: float
    ego_pose: Pose
    rgb_front: Optional[np.ndarray] = None
    route_command: Optional[str] = None
    #: Upcoming route points in world coordinates, nearest first. Every real
    #: driving stack is given a route; inferring where to go from pixels alone
    #: is a different problem from driving.
    route_waypoints: list[tuple[float, float]] = field(default_factory=list)
    lead_vehicle: Optional[LeadVehicle] = None


@dataclass
class VehicleControlAction:
    """Low-level control output (spec section 12).

    Ranges: throttle 0..1, steer -1..1, brake 0..1.
    """

    throttle: float = 0.0
    steer: float = 0.0
    brake: float = 0.0
    hand_brake: bool = False

    def is_finite(self) -> bool:
        """False if the policy emitted NaN or inf (spec section 50)."""
        return all(
            math.isfinite(v) for v in (self.throttle, self.steer, self.brake)
        )

    def clamped(self) -> "VehicleControlAction":
        """Clamp into the legal range (spec section 73).

        The simulator, not the model, decides what is physically applicable.
        """
        return VehicleControlAction(
            throttle=min(max(self.throttle, 0.0), 1.0),
            steer=min(max(self.steer, -1.0), 1.0),
            brake=min(max(self.brake, 0.0), 1.0),
            hand_brake=bool(self.hand_brake),
        )


@dataclass
class TrajectoryPoint:
    """A point on a planned path, in the ego's frame at the time of planning.

    Forward is +x, right is +y, metres. Ego-relative rather than world
    coordinates because that is what a network can actually predict, and it
    stays valid if the pose estimate is off.
    """

    x: float
    y: float
    target_speed_mps: Optional[float] = None
    timestamp_s: Optional[float] = None


@dataclass
class TrajectoryAction:
    """Where to go, rather than what to do with the pedals (spec section 13).

    This is the output shape of most published driving models - TCP,
    Transfuser, InterFuser, LAV all emit waypoints. A controller in the
    simulator turns it into steering and pedals, so the model never has to know
    anything about this vehicle.
    """

    waypoints: list[TrajectoryPoint] = field(default_factory=list)

    def is_finite(self) -> bool:
        return all(
            math.isfinite(p.x) and math.isfinite(p.y) for p in self.waypoints
        ) and len(self.waypoints) > 0


# Applied when a policy fails: coast and brake gently, keep the wheel where it
# was rather than jerking it straight (spec section 50).
def safety_fallback(previous_steer: float = 0.0) -> VehicleControlAction:
    return VehicleControlAction(throttle=0.0, steer=previous_steer, brake=0.5)


@dataclass
class EpisodeResult:
    """Outcome of one closed-loop episode."""

    episode_id: str = ""
    status: str = "UNKNOWN"
    map_name: str = ""
    policy_name: str = ""
    scenario_id: str = ""
    termination_reason: str = ""

    ticks: int = 0
    simulated_seconds: float = 0.0
    wall_seconds: float = 0.0

    distance_m: float = 0.0
    average_speed_mps: float = 0.0
    max_speed_mps: float = 0.0

    camera_frames: int = 0
    inferences: int = 0
    invalid_actions: int = 0
    # Inferences that missed the model's deadline (spec section 50). Counted
    # separately from other failures: a slow model is a different problem from
    # a wrong one.
    model_timeouts: int = 0

    inference_latency_ms_p50: float = 0.0
    inference_latency_ms_p95: float = 0.0

    collisions: int = 0
    lane_invasions: int = 0
    scenario_triggered: bool = False
    scenario_triggered_at: Optional[float] = None

    # Filled in by the evaluation engine when one is configured.
    score: Optional[float] = None
    result: str = ""
    minimum_ttc_s: Optional[float] = None
    route_completion_percent: Optional[float] = None

    versions: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=float), pct))
