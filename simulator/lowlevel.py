"""The low-level controller, and the executor that drives it from a decision.

Spec section 14 is explicit about the division: a high-level policy emits a
manoeuvre and "a conventional controller executes it". So the controller lives
here, in the simulator, not in a model - a model that had to know this vehicle's
throttle map could not be swapped for another.

`LaneKeepingController` is the same pure-pursuit + IDM arithmetic the PID
baseline uses; the baseline now calls into this rather than keeping a second
copy, because two copies of a controller diverge.

`DecisionExecutor` turns KEEP_LANE / SLOW_DOWN / BRAKE / CHANGE_* into inputs
for that controller. This is what makes a 7B VLM viable as a driver: it answers
in about 100 ms, which is fine for a decision every half second and hopeless for
steering at 20 Hz. The controller runs every tick regardless of when the last
decision arrived.
"""

from __future__ import annotations

import math
from typing import Optional

from simulator.types import (
    DECISIONS,
    HighLevelDecision,
    Observation,
    TrajectoryAction,
    VehicleControlAction,
)


class LaneKeepingController:
    """Pure pursuit onto the route, IDM for speed."""

    def __init__(
        self,
        target_speed_mps: float = 15.0,
        # Lateral
        steer_gain: float = 0.85,
        min_lookahead_m: float = 6.0,
        lookahead_time_s: float = 0.7,
        max_lookahead_m: float = 11.0,
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
        use_car_following: bool = True,
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
        #: Whether IDM reacts to the vehicle ahead. On for the PID baseline,
        #: which is an expert; off underneath a high-level policy, so that
        #: policy's decisions are the only thing preventing a rear-end.
        self.use_car_following = use_car_following

        self._dt = 0.05
        #: The unmodified cruise target. DecisionExecutor scales target_speed
        #: per decision and needs somewhere to scale *from*.
        self.cruise_speed = target_speed_mps

    def reset(self, fixed_delta_seconds: float = 0.05,
              target_speed_mps: float | None = None) -> None:
        self._dt = float(fixed_delta_seconds or 0.05)
        if target_speed_mps:
            self.target_speed = float(target_speed_mps)
        self.cruise_speed = self.target_speed

    def control(self, observation: Observation,
                lateral_offset_m: float = 0.0) -> VehicleControlAction:
        throttle, brake = self._longitudinal_from_idm(observation)
        return VehicleControlAction(
            throttle=throttle,
            steer=self._steer(observation, lateral_offset_m),
            brake=brake,
        )

    # -- lateral ----------------------------------------------------------
    def _steer(self, obs: Observation, lateral_offset_m: float = 0.0) -> float:
        target = self._lookahead_point(obs)
        if target is None:
            return 0.0

        dx = target[0] - obs.ego_pose.x
        dy = target[1] - obs.ego_pose.y

        # A lane change is a sideways shift of the pursuit target, applied
        # perpendicular to the ego's heading. The car then steers towards it
        # and its own dynamics shape the manoeuvre, exactly as the scenario
        # NPC's cut-in works.
        if lateral_offset_m:
            heading = math.radians(obs.ego_pose.yaw)
            dx += -math.sin(heading) * lateral_offset_m
            dy += math.cos(heading) * lateral_offset_m
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
        if self.target_speed > 0.1:
            speed_term = (v / self.target_speed) ** 4
        else:
            # A target of zero must mean "stop", but (v/v0)^4 with v0=0 was
            # guarded to 0, which makes IDM demand *full acceleration* towards a
            # standstill. Saturate the term instead, so a zero target reads as
            # "far above target" and the model decelerates.
            speed_term = 1.0 + v
        gap_term = 0.0

        lead = obs.lead_vehicle if self.use_car_following else None
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


class DecisionExecutor:
    """Executes a high-level manoeuvre with the low-level controller.

    A decision is *held* until the next one arrives: the model is asked a few
    times a second, the controller runs every tick, and in between the last
    instruction stands. That is how a real stack is layered, and it is the only
    way a 100 ms model can drive a 20 Hz loop.

    The mapping is deliberately blunt - each decision changes the controller's
    target, not its internals:

    * KEEP_LANE     cruise
    * SLOW_DOWN     cruise at 40%
    * BRAKE         demand a stop, and put the brake on directly rather than
                    waiting for the speed error to build
    * CHANGE_LEFT   aim at the neighbouring lane by offsetting the pursuit
      CHANGE_RIGHT  target sideways; the controller's own dynamics do the rest
    """

    #: Fraction of cruise speed each decision asks for.
    SPEED_FACTOR = {
        "KEEP_LANE": 1.0,
        "SLOW_DOWN": 0.4,
        "BRAKE": 0.0,
        "CHANGE_LEFT": 0.8,
        "CHANGE_RIGHT": 0.8,
    }

    #: Metres to offset the steering target for a lane change.
    LANE_OFFSET_M = 3.5

    def __init__(self, controller: Optional[LaneKeepingController] = None,
                 brake_floor: float = 0.85) -> None:
        # Car-following is deliberately OFF here.
        #
        # With it on, the first closed-loop VLM run scored 98.3 while emitting
        # KEEP_LANE on 400 out of 400 inferences: IDM handled the cut-in on its
        # own and the score measured the controller, not the model. A decision
        # module whose decisions change nothing cannot be evaluated, and a
        # federated experiment built on that number would be measuring noise.
        #
        # Without it the controller keeps the lane and holds cruise speed, and
        # nothing but the high-level decision will slow the car down.
        self.controller = controller or LaneKeepingController(
            use_car_following=False
        )
        self.brake_floor = brake_floor
        self.decision = "KEEP_LANE"
        self.reason = ""
        #: Counts of each decision actually executed, for the episode record.
        self.counts: dict[str, int] = {d: 0 for d in DECISIONS}

    def reset(self, fixed_delta_seconds: float = 0.05,
              target_speed_mps: float | None = None) -> None:
        self.controller.reset(fixed_delta_seconds, target_speed_mps)
        self.decision = "KEEP_LANE"
        self.reason = ""
        self.counts = {d: 0 for d in DECISIONS}

    def set_decision(self, decision: HighLevelDecision) -> bool:
        """Accept a new instruction. False if it was not a known manoeuvre."""
        if not decision.is_valid():
            return False
        self.decision = decision.decision
        self.reason = decision.reason
        self.counts[self.decision] += 1
        return True

    def control(self, observation: Observation) -> VehicleControlAction:
        cruise = self.controller.cruise_speed
        self.controller.target_speed = cruise * self.SPEED_FACTOR[self.decision]

        lateral = 0.0
        if self.decision == "CHANGE_LEFT":
            lateral = -self.LANE_OFFSET_M
        elif self.decision == "CHANGE_RIGHT":
            lateral = self.LANE_OFFSET_M

        action = self.controller.control(observation, lateral_offset_m=lateral)

        # BRAKE bypasses the speed loop entirely. Routing it through IDM meant
        # the firmest stop request produced *less* braking than SLOW_DOWN
        # (0.60 against 1.00), which is precisely backwards. Steering still
        # comes from the controller - braking is not a reason to stop steering.
        if self.decision == "BRAKE":
            return VehicleControlAction(
                throttle=0.0, steer=action.steer,
                brake=max(self.brake_floor, action.brake),
            )
        return action
