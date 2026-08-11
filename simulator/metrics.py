"""The evaluation engine.

Everything here is deterministic arithmetic over recorded state (spec section
84.11). Collision, TTC and score are computed, never judged; the same episode
scores the same number every time.

The engine is fed per-tick state by the worker and produces one
`EpisodeMetrics` at the end, which is what lands in metrics.json.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from simulator.route import Route
from simulator.types import percentile

INF = float("inf")


@dataclass
class EpisodeMetrics:
    """The scored outcome of an episode (spec sections 30-36, 40)."""

    # Safety
    collision_count: int = 0
    minimum_ttc_s: Optional[float] = None
    ttc_warning_ticks: int = 0
    ttc_dangerous_ticks: int = 0
    lane_invasion_count: int = 0

    # Progress
    route_completion_percent: float = 0.0
    distance_m: float = 0.0

    # Motion and comfort
    average_speed_mps: float = 0.0
    max_speed_mps: float = 0.0
    max_longitudinal_decel_mps2: float = 0.0
    max_lateral_acceleration_mps2: float = 0.0
    max_jerk_mps3: float = 0.0
    hard_brake_count: int = 0

    # Model runtime (spec section 35)
    inference_latency_ms_p50: float = 0.0
    inference_latency_ms_p95: float = 0.0
    inference_latency_ms_p99: float = 0.0
    model_timeouts: int = 0

    episode_duration_s: float = 0.0

    # Verdict
    score: float = 0.0
    result: str = "UNKNOWN"
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationEngine:
    """Accumulates per-tick state and scores the episode."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        ttc = config["ttc"]
        self.corridor_half_width = float(ttc["corridor_half_width_m"])
        self.min_closing_speed = float(ttc["min_closing_speed_mps"])
        self.vehicle_extent = float(ttc["vehicle_extent_m"])
        self.warning_below = float(ttc["warning_below_s"])
        self.dangerous_below = float(ttc["dangerous_below_s"])
        self.hard_brake_threshold = float(config["comfort"]["hard_brake_mps2"])
        self.hard_brake_release = float(
            config["comfort"].get("hard_brake_release_mps2",
                                  self.hard_brake_threshold / 2.0)
        )

        self.metrics = EpisodeMetrics()
        self._speed_sum = 0.0
        self._ticks = 0
        self._previous_accel: Optional[float] = None
        self._braking = False  # edge detector, so one brake is not counted 40 times

    # -- per tick ---------------------------------------------------------
    def update(
        self,
        dt: float,
        speed_mps: float,
        longitudinal_accel: float,
        lateral_accel: float,
        ttc_s: Optional[float],
    ) -> None:
        self._ticks += 1
        self._speed_sum += speed_mps

        m = self.metrics
        m.max_speed_mps = max(m.max_speed_mps, speed_mps)
        m.max_longitudinal_decel_mps2 = min(
            m.max_longitudinal_decel_mps2, longitudinal_accel
        )
        m.max_lateral_acceleration_mps2 = max(
            m.max_lateral_acceleration_mps2, abs(lateral_accel)
        )

        if self._previous_accel is not None and dt > 0:
            jerk = abs(longitudinal_accel - self._previous_accel) / dt
            m.max_jerk_mps3 = max(m.max_jerk_mps3, jerk)
        self._previous_accel = longitudinal_accel

        # Count a hard brake once per braking event, not once per tick, and
        # use a Schmitt trigger rather than a bare threshold: real deceleration
        # wanders either side of the limit during one manoeuvre, and a plain
        # edge detector turns a single brake into a handful. Measured on one
        # episode: 9 counted, 2 actually performed.
        if self._braking:
            if longitudinal_accel > self.hard_brake_release:
                self._braking = False
        elif longitudinal_accel <= self.hard_brake_threshold:
            m.hard_brake_count += 1
            self._braking = True

        if ttc_s is not None and math.isfinite(ttc_s):
            if m.minimum_ttc_s is None or ttc_s < m.minimum_ttc_s:
                m.minimum_ttc_s = ttc_s
            if ttc_s < self.dangerous_below:
                m.ttc_dangerous_ticks += 1
            elif ttc_s < self.warning_below:
                m.ttc_warning_ticks += 1

    # -- TTC --------------------------------------------------------------
    def time_to_collision(
        self,
        gap_m: float,
        lateral_m: float,
        closing_speed_mps: float,
    ) -> Optional[float]:
        """TTC against one vehicle, or None if it is not a threat.

        A vehicle only counts when it is ahead, inside the lateral corridor,
        and actually being closed on. Anything else has no meaningful TTC -
        returning a huge number instead would quietly drag the minimum around.
        """
        if abs(lateral_m) > self.corridor_half_width:
            return None
        clear_gap = gap_m - self.vehicle_extent
        if clear_gap <= 0.0:
            return 0.0  # already overlapping
        if closing_speed_mps < self.min_closing_speed:
            return None
        return clear_gap / closing_speed_mps

    # -- final ------------------------------------------------------------
    def finish(
        self,
        collision_count: int,
        lane_invasion_count: int,
        route: Optional[Route],
        final_x: float,
        final_y: float,
        distance_m: float,
        duration_s: float,
        latencies_ms: list[float],
        model_timeouts: int = 0,
    ) -> EpisodeMetrics:
        m = self.metrics
        m.collision_count = collision_count
        m.lane_invasion_count = lane_invasion_count
        m.distance_m = distance_m
        m.episode_duration_s = duration_s
        m.average_speed_mps = self._speed_sum / max(1, self._ticks)
        m.route_completion_percent = (
            route.completion_percent(final_x, final_y) if route else 0.0
        )
        m.inference_latency_ms_p50 = percentile(latencies_ms, 50)
        m.inference_latency_ms_p95 = percentile(latencies_ms, 95)
        m.inference_latency_ms_p99 = percentile(latencies_ms, 99)
        m.model_timeouts = model_timeouts

        m.score, m.score_breakdown = self._score(m)
        # Any collision fails the scenario (spec section 31).
        m.result = "FAIL" if m.collision_count > 0 else "PASS"
        return m

    def _score(self, m: EpisodeMetrics) -> tuple[float, dict[str, float]]:
        weights = self.config["score"]
        breakdown: dict[str, float] = {"base": float(weights["base"])}

        if m.collision_count > 0:
            breakdown["collision"] = float(weights["collision"])

        # TTC bands do not stack: the worst one that applies is charged.
        if m.minimum_ttc_s is not None:
            if m.minimum_ttc_s < 1.0:
                breakdown["ttc_below_1s"] = float(weights["ttc_below_1s"])
            elif m.minimum_ttc_s < 2.0:
                breakdown["ttc_below_2s"] = float(weights["ttc_below_2s"])

        if m.lane_invasion_count:
            breakdown["lane_invasion"] = (
                m.lane_invasion_count * float(weights["lane_invasion_each"])
            )
        if m.hard_brake_count:
            breakdown["hard_brake"] = (
                m.hard_brake_count * float(weights["hard_brake_each"])
            )

        shortfall = 100.0 - m.route_completion_percent
        if shortfall > 0:
            breakdown["route_incomplete"] = round(
                -shortfall * float(weights["route_incomplete_factor"]), 4
            )

        total = max(0.0, sum(breakdown.values()))
        return round(total, 2), breakdown
