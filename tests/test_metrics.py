"""Tests for the evaluation engine.

Scoring must be deterministic arithmetic (spec section 84.11), so it is tested
as arithmetic: given these events, the score is exactly this number. No CARLA
is involved.
"""

from __future__ import annotations

import pytest
import yaml

from simulator.config import CONFIG_DIR
from simulator.metrics import EvaluationEngine
from simulator.route import Route


@pytest.fixture
def config():
    with (CONFIG_DIR / "evaluation.yaml").open() as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def engine(config):
    return EvaluationEngine(config)


def finish(engine, *, collisions=0, lane_invasions=0, route=None,
           x=0.0, y=0.0, distance=100.0, duration=20.0, latencies=None):
    return engine.finish(
        collision_count=collisions,
        lane_invasion_count=lane_invasions,
        route=route,
        final_x=x, final_y=y,
        distance_m=distance,
        duration_s=duration,
        latencies_ms=latencies if latencies is not None else [1.0, 2.0, 3.0],
    )


class StubRoute(Route):
    """A route whose completion is whatever the test says it is."""

    def __init__(self, percent: float):
        super().__init__(points=[(0.0, 0.0)], cumulative=[0.0])
        self._percent = percent

    def completion_percent(self, x: float, y: float) -> float:
        return self._percent


# --- TTC -----------------------------------------------------------------
class TestTimeToCollision:
    def test_straightforward_case(self, engine):
        # 24.5 m centre-to-centre, 4.5 m of vehicle, closing at 10 m/s -> 2 s.
        assert engine.time_to_collision(24.5, 0.0, 10.0) == pytest.approx(2.0)

    def test_a_car_in_the_next_lane_is_not_a_threat(self, engine):
        assert engine.time_to_collision(24.5, 3.5, 10.0) is None

    def test_a_car_pulling_away_has_no_ttc(self, engine):
        """Undefined, not enormous - a huge number would drag the minimum."""
        assert engine.time_to_collision(24.5, 0.0, -5.0) is None

    def test_barely_closing_has_no_ttc(self, engine):
        assert engine.time_to_collision(24.5, 0.0, 0.01) is None

    def test_overlapping_is_zero(self, engine):
        assert engine.time_to_collision(2.0, 0.0, 5.0) == 0.0

    def test_minimum_is_kept_across_ticks(self, engine):
        for ttc in (8.0, 3.5, 5.0):
            engine.update(0.05, 10.0, 0.0, 0.0, ttc)
        assert engine.metrics.minimum_ttc_s == pytest.approx(3.5)

    def test_bands_are_counted(self, engine):
        for ttc in (2.5, 2.9, 1.5, 6.0):
            engine.update(0.05, 10.0, 0.0, 0.0, ttc)
        assert engine.metrics.ttc_warning_ticks == 2    # 2.5 and 2.9
        assert engine.metrics.ttc_dangerous_ticks == 1  # 1.5

    def test_no_vehicle_ahead_leaves_ttc_unset(self, engine):
        engine.update(0.05, 10.0, 0.0, 0.0, None)
        assert engine.metrics.minimum_ttc_s is None


# --- comfort -------------------------------------------------------------
class TestComfort:
    def test_a_single_brake_is_counted_once(self, engine):
        """Not once per tick: a 1 s hard brake at 20 Hz is one event."""
        for _ in range(20):
            engine.update(0.05, 10.0, -6.0, 0.0, None)
        assert engine.metrics.hard_brake_count == 1

    def test_separate_brakes_are_counted_separately(self, engine):
        for accel in (-6.0, -6.0, 0.0, 0.0, -6.0):
            engine.update(0.05, 10.0, accel, 0.0, None)
        assert engine.metrics.hard_brake_count == 2

    def test_gentle_braking_is_not_a_hard_brake(self, engine):
        for _ in range(10):
            engine.update(0.05, 10.0, -2.0, 0.0, None)
        assert engine.metrics.hard_brake_count == 0

    def test_peak_deceleration_is_tracked(self, engine):
        for accel in (-1.0, -5.5, -2.0):
            engine.update(0.05, 10.0, accel, 0.0, None)
        assert engine.metrics.max_longitudinal_decel_mps2 == pytest.approx(-5.5)

    def test_lateral_acceleration_uses_magnitude(self, engine):
        engine.update(0.05, 10.0, 0.0, -3.0, None)
        assert engine.metrics.max_lateral_acceleration_mps2 == pytest.approx(3.0)

    def test_jerk_is_the_rate_of_change_of_acceleration(self, engine):
        engine.update(0.1, 10.0, 1.0, 0.0, None)
        engine.update(0.1, 10.0, 2.0, 0.0, None)   # +1 m/s^2 over 0.1 s
        assert engine.metrics.max_jerk_mps3 == pytest.approx(10.0)


# --- scoring (spec section 36) -------------------------------------------
class TestScoring:
    def test_a_clean_run_scores_100(self, engine):
        m = finish(engine, route=StubRoute(100.0))
        assert m.score == 100.0
        assert m.result == "PASS"

    def test_a_collision_zeroes_the_score_and_fails(self, engine):
        m = finish(engine, collisions=1, route=StubRoute(100.0))
        assert m.score == 0.0
        assert m.result == "FAIL"

    def test_lane_invasions_cost_ten_each(self, engine):
        m = finish(engine, lane_invasions=3, route=StubRoute(100.0))
        assert m.score == 70.0
        assert m.result == "PASS"

    def test_hard_brakes_cost_three_each(self, engine):
        for accel in (-6.0, 0.0, -6.0):
            engine.update(0.05, 10.0, accel, 0.0, None)
        m = finish(engine, route=StubRoute(100.0))
        assert m.score == 94.0

    def test_incomplete_route_is_charged_proportionally(self, engine):
        # 60% complete -> (100 - 60) * 0.2 = 8 points.
        m = finish(engine, route=StubRoute(60.0))
        assert m.score == pytest.approx(92.0)

    def test_ttc_below_two_costs_fifteen(self, engine):
        engine.update(0.05, 10.0, 0.0, 0.0, 1.5)
        m = finish(engine, route=StubRoute(100.0))
        assert m.score == 85.0

    def test_ttc_below_one_costs_thirty(self, engine):
        engine.update(0.05, 10.0, 0.0, 0.0, 0.5)
        m = finish(engine, route=StubRoute(100.0))
        assert m.score == 70.0

    def test_ttc_bands_do_not_stack(self, engine):
        """A 0.5 s minimum is charged 30, not 30 + 15."""
        engine.update(0.05, 10.0, 0.0, 0.0, 0.5)
        m = finish(engine, route=StubRoute(100.0))
        assert m.score_breakdown.get("ttc_below_2s") is None
        assert m.score_breakdown["ttc_below_1s"] == -30.0

    def test_score_never_goes_negative(self, engine):
        m = finish(engine, collisions=1, lane_invasions=20, route=StubRoute(0.0))
        assert m.score == 0.0

    def test_breakdown_sums_to_the_score(self, engine):
        m = finish(engine, lane_invasions=2, route=StubRoute(80.0))
        assert sum(m.score_breakdown.values()) == pytest.approx(m.score)

    def test_scoring_is_repeatable(self, config):
        scores = []
        for _ in range(3):
            e = EvaluationEngine(config)
            e.update(0.05, 10.0, -6.0, 0.0, 1.8)
            scores.append(finish(e, lane_invasions=1, route=StubRoute(75.0)).score)
        assert len(set(scores)) == 1


class TestLatencyPercentiles:
    def test_percentiles_are_reported(self, engine):
        m = finish(engine, route=StubRoute(100.0),
                   latencies=[float(i) for i in range(1, 101)])
        assert m.inference_latency_ms_p50 == pytest.approx(50.5)
        assert m.inference_latency_ms_p95 > m.inference_latency_ms_p50
        assert m.inference_latency_ms_p99 >= m.inference_latency_ms_p95


# --- route ---------------------------------------------------------------
class TestRoute:
    def _straight(self):
        route = Route()
        route.points = [(float(i), 0.0) for i in range(0, 101, 10)]
        route.cumulative = [float(i) for i in range(0, 101, 10)]
        return route

    def test_length(self):
        assert self._straight().length_m == 100.0

    def test_progress_at_the_start_and_end(self):
        route = self._straight()
        assert route.completion_percent(0.0, 0.0) == 0.0
        assert route.completion_percent(100.0, 0.0) == 100.0

    def test_progress_in_the_middle(self):
        assert self._straight().completion_percent(50.0, 0.0) == pytest.approx(50.0)

    def test_progress_uses_the_nearest_point_not_the_straight_line(self):
        # Standing beside the route, not on it, still counts as progress there.
        assert self._straight().completion_percent(30.0, 4.0) == pytest.approx(30.0)

    def test_completion_is_clamped(self):
        route = self._straight()
        assert 0.0 <= route.completion_percent(500.0, 0.0) <= 100.0

    def test_an_empty_route_is_zero_percent(self):
        assert Route().completion_percent(10.0, 10.0) == 0.0
