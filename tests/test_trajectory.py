"""Tests for trajectory actions and the controller that executes them.

Waypoint output is the interface most published driving models expose, so the
conversion from a path to pedals is worth pinning down. No CARLA involved.
"""

from __future__ import annotations

import math

import pytest

from simulator.control import TrajectoryController
from simulator.types import TrajectoryAction, TrajectoryPoint


def straight(distance_per_point: float = 4.0, count: int = 4,
             speed: float | None = None) -> TrajectoryAction:
    return TrajectoryAction(waypoints=[
        TrajectoryPoint(x=distance_per_point * (i + 1), y=0.0,
                        target_speed_mps=speed, timestamp_s=0.5 * (i + 1))
        for i in range(count)
    ])


@pytest.fixture
def controller():
    return TrajectoryController()


class TestValidity:
    def test_an_empty_trajectory_is_not_finite(self):
        assert not TrajectoryAction().is_finite()

    def test_nan_waypoints_are_rejected(self):
        action = TrajectoryAction(waypoints=[TrajectoryPoint(x=math.nan, y=0.0)])
        assert not action.is_finite()

    def test_an_ordinary_path_is_finite(self):
        assert straight().is_finite()


class TestSteering:
    def test_a_straight_path_steers_straight(self, controller):
        control = controller.control(straight(), speed_mps=10.0, dt=0.05)
        assert control.steer == pytest.approx(0.0, abs=1e-6)

    def test_a_path_to_the_right_steers_right(self, controller):
        action = TrajectoryAction(waypoints=[
            TrajectoryPoint(x=8.0, y=3.0, timestamp_s=0.5),
        ])
        assert controller.control(action, speed_mps=10.0, dt=0.05).steer > 0

    def test_a_path_to_the_left_steers_left(self, controller):
        action = TrajectoryAction(waypoints=[
            TrajectoryPoint(x=8.0, y=-3.0, timestamp_s=0.5),
        ])
        assert controller.control(action, speed_mps=10.0, dt=0.05).steer < 0

    def test_steering_stays_in_range_for_a_sharp_path(self, controller):
        action = TrajectoryAction(waypoints=[TrajectoryPoint(x=0.5, y=9.0)])
        assert -1.0 <= controller.control(action, 10.0, 0.05).steer <= 1.0


class TestSpeed:
    def test_an_explicit_target_speed_is_used(self, controller):
        assert controller.target_speed(straight(speed=12.0), 0.05) == 12.0

    def test_otherwise_the_speed_comes_from_the_spacing(self, controller):
        # Furthest point 16 m away at t=2.0 s -> 8 m/s.
        assert controller.target_speed(straight(), 0.05) == pytest.approx(8.0)

    def test_it_accelerates_when_below_the_implied_speed(self, controller):
        control = controller.control(straight(speed=14.0), speed_mps=5.0, dt=0.05)
        assert control.throttle > 0 and control.brake == 0

    def test_it_brakes_when_well_above_the_implied_speed(self, controller):
        control = controller.control(straight(speed=2.0), speed_mps=15.0, dt=0.05)
        assert control.brake > 0 and control.throttle == 0

    def test_a_stationary_path_asks_for_a_stop(self, controller):
        control = controller.control(straight(speed=0.0), speed_mps=12.0, dt=0.05)
        assert control.brake > 0

    def test_an_empty_path_coasts_to_a_stop(self, controller):
        control = controller.control(TrajectoryAction(), speed_mps=10.0, dt=0.05)
        assert control.throttle == 0.0 and control.brake > 0

    def test_output_is_always_applicable(self, controller):
        for speed in (0.0, 5.0, 15.0, 30.0):
            control = controller.control(straight(speed=10.0), speed, 0.05)
            assert control.is_finite()
            assert control.clamped() == control
