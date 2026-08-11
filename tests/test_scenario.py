"""Tests for the scenario engine.

The geometry and the trigger/action logic are the parts that decide whether a
scenario fires correctly, and they are pure arithmetic over actor poses. They
are tested here with lightweight stand-ins for CARLA actors so they run in CI
without a simulator (spec section 72).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from simulator.scenario import (
    ACTIONS,
    TRIGGERS,
    CutInAction,
    ElapsedTimeTrigger,
    RelativeDistanceTrigger,
    ScenarioConfig,
    ScenarioContext,
    ScenarioRunner,
    lateral_offset,
    load_scenario,
    longitudinal_gap,
)


# --- stand-ins for CARLA actors -----------------------------------------
@dataclass
class FakeVector:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class FakeTransform:
    """Enough of carla.Transform for the geometry helpers."""

    def __init__(self, x: float, y: float, yaw_deg: float):
        self.location = FakeVector(x, y, 0.0)
        self.yaw = yaw_deg

    def get_forward_vector(self) -> FakeVector:
        r = math.radians(self.yaw)
        return FakeVector(math.cos(r), math.sin(r), 0.0)

    def get_right_vector(self) -> FakeVector:
        r = math.radians(self.yaw)
        return FakeVector(-math.sin(r), math.cos(r), 0.0)


class FakeActor:
    def __init__(self, x: float, y: float, yaw_deg: float = 0.0):
        self._tf = FakeTransform(x, y, yaw_deg)

    def get_transform(self):
        return self._tf

    def get_location(self):
        return self._tf.location


class FakeController:
    def __init__(self):
        self.blend = 0.0
        self.blend_target_side = None


def context(ego, npc, sim_time=0.0, dt=0.05, controller=None):
    return ScenarioContext(
        world=None, map=None, ego=ego, npc=npc,
        npc_controller=controller, sim_time=sim_time, dt=dt,
    )


# --- geometry ------------------------------------------------------------
class TestGeometry:
    def test_gap_is_positive_when_ahead(self):
        assert longitudinal_gap(FakeActor(0, 0), FakeActor(20, 0)) == pytest.approx(20.0)

    def test_gap_is_negative_when_behind(self):
        assert longitudinal_gap(FakeActor(0, 0), FakeActor(-15, 0)) == pytest.approx(-15.0)

    def test_gap_follows_heading_not_world_axes(self):
        # Ego facing north; a car 20 m north is ahead, not sideways.
        ego = FakeActor(0, 0, yaw_deg=90)
        assert longitudinal_gap(ego, FakeActor(0, 20)) == pytest.approx(20.0)

    def test_a_car_alongside_has_almost_no_gap(self):
        # 3.5 m to the side, level with the ego: not "3.5 m ahead".
        assert longitudinal_gap(FakeActor(0, 0), FakeActor(0, 3.5)) == pytest.approx(0.0)

    def test_lateral_offset_sign(self):
        ego = FakeActor(0, 0, yaw_deg=0)
        assert lateral_offset(ego, FakeActor(0, 3.5)) > 0    # CARLA: +y is right
        assert lateral_offset(ego, FakeActor(0, -3.5)) < 0


# --- triggers ------------------------------------------------------------
class TestRelativeDistanceTrigger:
    def test_at_most_fires_once_inside_range(self):
        trigger = RelativeDistanceTrigger({"distance_m": 12, "comparison": "at_most"})
        assert not trigger.check(context(FakeActor(0, 0), FakeActor(20, 0)))
        assert trigger.check(context(FakeActor(0, 0), FakeActor(10, 0)))

    def test_at_least_fires_once_the_lead_is_built(self):
        trigger = RelativeDistanceTrigger({"distance_m": 12, "comparison": "at_least"})
        assert not trigger.check(context(FakeActor(0, 0), FakeActor(5, 0)))
        assert trigger.check(context(FakeActor(0, 0), FakeActor(13, 0)))

    def test_a_vehicle_behind_never_fires(self):
        """The bug this guards: with at_most, an overtaking car satisfies the
        condition the instant it draws level and cuts across the ego."""
        for comparison in ("at_most", "at_least"):
            trigger = RelativeDistanceTrigger(
                {"distance_m": 12, "comparison": comparison}
            )
            assert not trigger.check(context(FakeActor(0, 0), FakeActor(-5, 0)))

    def test_default_comparison_is_at_most(self):
        assert RelativeDistanceTrigger({"distance_m": 12}).comparison == "at_most"

    def test_unknown_comparison_is_rejected(self):
        with pytest.raises(ValueError, match="comparison"):
            RelativeDistanceTrigger({"distance_m": 12, "comparison": "nearby"})

    def test_no_npc_means_no_trigger(self):
        trigger = RelativeDistanceTrigger({"distance_m": 12})
        assert not trigger.check(context(FakeActor(0, 0), None))


class TestElapsedTimeTrigger:
    def test_fires_at_its_time(self):
        trigger = ElapsedTimeTrigger({"at_seconds": 5.0})
        assert not trigger.check(context(FakeActor(0, 0), None, sim_time=4.9))
        assert trigger.check(context(FakeActor(0, 0), None, sim_time=5.0))


# --- actions -------------------------------------------------------------
class TestCutInAction:
    def test_it_cuts_towards_the_ego(self):
        action = CutInAction({"duration_seconds": 2.0})
        controller = FakeController()
        # Ego sits to the right of the NPC.
        ctx = context(FakeActor(0, 3.5), FakeActor(0, 0), controller=controller)
        data = action.start(ctx)
        assert data["side"] == "right"
        assert controller.blend_target_side == "right"

    def test_blend_ramps_over_the_duration(self):
        action = CutInAction({"duration_seconds": 1.0})
        controller = FakeController()
        ctx = context(FakeActor(0, 3.5), FakeActor(0, 0), dt=0.5, controller=controller)
        action.start(ctx)

        assert action.update(ctx) is False
        assert controller.blend == pytest.approx(0.5)
        assert action.update(ctx) is True
        assert action.finished

    def test_blend_is_released_on_completion(self):
        """Otherwise the NPC keeps steering towards the *next* lane over and
        drifts sideways across the carriageway after the manoeuvre."""
        action = CutInAction({"duration_seconds": 1.0})
        controller = FakeController()
        ctx = context(FakeActor(0, 3.5), FakeActor(0, 0), dt=1.0, controller=controller)
        action.start(ctx)
        action.update(ctx)

        assert action.finished
        assert controller.blend == 0.0
        assert controller.blend_target_side is None

    def test_update_after_completion_is_idempotent(self):
        action = CutInAction({"duration_seconds": 1.0})
        controller = FakeController()
        ctx = context(FakeActor(0, 3.5), FakeActor(0, 0), dt=2.0, controller=controller)
        action.start(ctx)
        action.update(ctx)
        assert action.update(ctx) is True


# --- configuration -------------------------------------------------------
class TestScenarioConfig:
    def test_the_shipped_cut_in_scenario_loads(self):
        scenario = load_scenario("highway_cut_in")
        assert scenario.id == "highway_cut_in_001"
        assert scenario.map == "Town04"
        assert scenario.seed == 42
        assert scenario.scenario_vehicle["relative_lane"] == "left"
        assert scenario.trigger["type"] == "relative_distance"
        assert scenario.action["type"] == "cut_in"

    def test_the_npc_starts_behind_so_it_can_overtake(self):
        scenario = load_scenario("highway_cut_in")
        assert scenario.scenario_vehicle["initial_longitudinal_distance_m"] < 0
        assert scenario.trigger["comparison"] == "at_least"

    def test_missing_required_keys_are_reported(self):
        with pytest.raises(ValueError, match="missing required keys"):
            ScenarioConfig.from_dict({"id": "x", "name": "y"})

    def test_unknown_keys_are_ignored(self):
        scenario = ScenarioConfig.from_dict(
            {"id": "x", "name": "y", "map": "Town04", "future_field": 1}
        )
        assert scenario.id == "x"


class TestRunnerValidation:
    def _config(self, **overrides):
        base = {"id": "t", "name": "t", "map": "Town04"}
        base.update(overrides)
        return ScenarioConfig.from_dict(base)

    def test_unknown_trigger_type_is_rejected(self):
        with pytest.raises(ValueError, match="unknown trigger type"):
            ScenarioRunner(self._config(trigger={"type": "sunspots"}))

    def test_unknown_action_type_is_rejected(self):
        with pytest.raises(ValueError, match="unknown action type"):
            ScenarioRunner(self._config(action={"type": "barrel_roll"}))

    def test_registries_expose_the_built_in_types(self):
        assert "relative_distance" in TRIGGERS
        assert "elapsed_time" in TRIGGERS
        assert "cut_in" in ACTIONS

    def test_a_scenario_without_a_trigger_is_allowed(self):
        runner = ScenarioRunner(self._config())
        assert runner.trigger is None and runner.action is None

    def test_events_are_timestamped(self):
        runner = ScenarioRunner(self._config())
        runner.log_event(1.2345, "THING", {"a": 1})
        assert runner.events == [{"time": 1.234, "type": "THING", "data": {"a": 1}}]
