"""Unit tests for the simulator/policy contract.

These never touch CARLA (spec section 72): they cover the safety envelope the
worker applies to whatever a model returns, which is exactly the logic that
must not be trusted to a running simulator to validate.
"""

from __future__ import annotations

import math

import pytest

from simulator.types import (
    Observation,
    Pose,
    VehicleControlAction,
    percentile,
    safety_fallback,
)


class TestClamping:
    """The simulator, not the model, decides what is applicable (section 73)."""

    def test_values_in_range_are_untouched(self):
        a = VehicleControlAction(throttle=0.4, steer=-0.2, brake=0.1).clamped()
        assert (a.throttle, a.steer, a.brake) == (0.4, -0.2, 0.1)

    @pytest.mark.parametrize(
        "field,value,expected",
        [
            ("throttle", 1.7, 1.0),
            ("throttle", -0.5, 0.0),
            ("steer", 3.0, 1.0),
            ("steer", -3.0, -1.0),
            ("brake", 2.0, 1.0),
            ("brake", -1.0, 0.0),
        ],
    )
    def test_out_of_range_is_clamped(self, field, value, expected):
        action = VehicleControlAction(**{field: value}).clamped()
        assert getattr(action, field) == expected

    def test_clamped_returns_a_new_object(self):
        original = VehicleControlAction(throttle=5.0)
        assert original.clamped() is not original
        assert original.throttle == 5.0


class TestValidity:
    """Non-finite output must be rejected, not clamped (section 50)."""

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    @pytest.mark.parametrize("field", ["throttle", "steer", "brake"])
    def test_non_finite_is_rejected(self, field, bad):
        assert not VehicleControlAction(**{field: bad}).is_finite()

    def test_ordinary_values_are_finite(self):
        assert VehicleControlAction(throttle=0.4, steer=0.1, brake=0.0).is_finite()

    def test_clamping_cannot_rescue_nan(self):
        # min/max silently propagate NaN, so is_finite must be checked first.
        assert not VehicleControlAction(throttle=math.nan).clamped().is_finite()


class TestSafetyFallback:
    """What gets applied when a policy fails (section 50)."""

    def test_coasts_and_brakes_while_holding_the_wheel(self):
        action = safety_fallback(previous_steer=0.3)
        assert action.throttle == 0.0
        assert action.brake == 0.5
        assert action.steer == 0.3

    def test_fallback_is_itself_legal(self):
        action = safety_fallback(previous_steer=0.3)
        assert action.is_finite()
        assert action.clamped() == action


class TestPose:
    def test_distance(self):
        assert Pose(x=3.0, y=4.0).distance_to(Pose()) == pytest.approx(5.0)

    def test_distance_is_symmetric(self):
        a, b = Pose(x=1.0, y=2.0, z=3.0), Pose(x=-4.0, y=0.5, z=2.0)
        assert a.distance_to(b) == pytest.approx(b.distance_to(a))


class TestObservation:
    def test_sensors_that_are_not_mounted_stay_none(self):
        obs = Observation(
            frame_id=1, timestamp=0.05, speed_mps=3.0,
            acceleration_mps2=0.5, steering_angle=0.0, ego_pose=Pose(),
        )
        assert obs.rgb_front is None
        assert obs.route_command is None


class TestPercentile:
    def test_empty_is_zero(self):
        assert percentile([], 95) == 0.0

    def test_p50_of_a_ramp(self):
        assert percentile([float(i) for i in range(1, 102)], 50) == pytest.approx(51.0)
