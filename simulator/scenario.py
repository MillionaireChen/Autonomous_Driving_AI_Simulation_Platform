"""The scenario engine.

Scenarios are YAML, never Python (spec section 84.9). Nothing in this module
knows what "highway cut-in" means: it reads a trigger type and an action type
out of a file and looks them up in a registry. Adding a new scenario is a new
YAML file; adding a new *kind* of scenario is one new Trigger or Action class.

    trigger fires  ->  action starts  ->  action runs to completion
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import carla
import yaml

from simulator.npc import LaneFollower, speed_of

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios"


# --- geometry helpers ----------------------------------------------------
def longitudinal_gap(ego: carla.Actor, other: carla.Actor) -> float:
    """Distance from ego to other along ego's heading. Positive = ahead."""
    tf = ego.get_transform()
    forward = tf.get_forward_vector()
    dx = other.get_location().x - tf.location.x
    dy = other.get_location().y - tf.location.y
    return dx * forward.x + dy * forward.y


def lateral_offset(ego: carla.Actor, other: carla.Actor) -> float:
    """Offset of other from ego's centreline. Positive = to ego's right."""
    tf = ego.get_transform()
    right = tf.get_right_vector()
    dx = other.get_location().x - tf.location.x
    dy = other.get_location().y - tf.location.y
    return dx * right.x + dy * right.y


# --- configuration -------------------------------------------------------
@dataclass
class ScenarioConfig:
    id: str
    name: str
    map: str
    seed: int = 42
    duration_seconds: float = 40.0
    weather: dict[str, Any] = field(default_factory=dict)
    ego: dict[str, Any] = field(default_factory=dict)
    traffic: dict[str, Any] = field(default_factory=dict)
    scenario_vehicle: dict[str, Any] = field(default_factory=dict)
    trigger: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    termination: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioConfig":
        known = {f for f in cls.__dataclass_fields__}
        missing = {"id", "name", "map"} - data.keys()
        if missing:
            raise ValueError(f"scenario is missing required keys: {sorted(missing)}")
        return cls(**{k: v for k, v in data.items() if k in known})


def load_scenario(name_or_path: str | Path) -> ScenarioConfig:
    path = Path(name_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{path}.yaml"
    elif not path.is_absolute():
        path = SCENARIO_DIR / path
    with path.open() as fh:
        return ScenarioConfig.from_dict(yaml.safe_load(fh))


# --- context -------------------------------------------------------------
@dataclass
class ScenarioContext:
    world: carla.World
    map: carla.Map
    ego: carla.Vehicle
    npc: Optional[carla.Vehicle]
    npc_controller: Optional[LaneFollower]
    sim_time: float = 0.0
    dt: float = 0.05


# --- triggers ------------------------------------------------------------
TRIGGERS: dict[str, Callable[[dict], "Trigger"]] = {}
ACTIONS: dict[str, Callable[[dict], "ScenarioAction"]] = {}


def register_trigger(name: str):
    def wrap(cls):
        TRIGGERS[name] = cls
        return cls
    return wrap


def register_action(name: str):
    def wrap(cls):
        ACTIONS[name] = cls
        return cls
    return wrap


class Trigger(ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def check(self, ctx: ScenarioContext) -> bool:
        """True once the scenario should act."""


@register_trigger("relative_distance")
class RelativeDistanceTrigger(Trigger):
    """Fires on the longitudinal gap between the ego and the scenario vehicle.

    The gap is measured along the ego's heading, so a vehicle level with the
    ego in the next lane does not read as "12 metres away" merely because it
    is 12 metres away in a straight line.

    `comparison` has to be explicit, because the sensible test depends on which
    way the gap is moving:

    * `at_most`  - the NPC is closing from ahead and has come within range.
    * `at_least` - the NPC is overtaking from behind and has built up a lead.

    Getting this wrong is silent: with `at_most`, a vehicle overtaking from
    behind satisfies the condition the instant it draws level, and cuts in
    across the ego's bonnet instead of in front of it.
    """

    COMPARISONS = ("at_most", "at_least")

    def __init__(self, config):
        super().__init__(config)
        self.distance_m = float(config["distance_m"])
        self.comparison = config.get("comparison", "at_most")
        if self.comparison not in self.COMPARISONS:
            raise ValueError(
                f"unknown comparison {self.comparison!r} "
                f"(expected one of {self.COMPARISONS})"
            )

    def check(self, ctx: ScenarioContext) -> bool:
        if ctx.npc is None:
            return False
        gap = longitudinal_gap(ctx.ego, ctx.npc)
        if gap < 0.0:  # still behind the ego; not a cut-in candidate yet
            return False
        return gap <= self.distance_m if self.comparison == "at_most" \
            else gap >= self.distance_m


@register_trigger("elapsed_time")
class ElapsedTimeTrigger(Trigger):
    """Fires at a fixed time. Useful for regression tests and warm-ups."""

    def __init__(self, config):
        super().__init__(config)
        self.at_seconds = float(config["at_seconds"])

    def check(self, ctx: ScenarioContext) -> bool:
        return ctx.sim_time >= self.at_seconds


# --- actions -------------------------------------------------------------
class ScenarioAction(ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.started = False
        self.finished = False

    @abstractmethod
    def start(self, ctx: ScenarioContext) -> dict[str, Any]:
        """Begin; return data describing what was started, for the event log."""

    @abstractmethod
    def update(self, ctx: ScenarioContext) -> bool:
        """Advance; return True when complete."""


@register_action("cut_in")
class CutInAction(ScenarioAction):
    """Move the scenario vehicle into the ego's lane over a fixed duration.

    Implemented as a lateral blend of the NPC's steering target rather than a
    scripted path, so the manoeuvre stays physical: the car steers into the
    lane and its own dynamics decide the rest.
    """

    def __init__(self, config):
        super().__init__(config)
        self.duration = float(config.get("duration_seconds", 2.0))
        self.elapsed = 0.0
        self.side: Optional[str] = None

    def start(self, ctx: ScenarioContext) -> dict[str, Any]:
        self.started = True
        self.elapsed = 0.0
        # Which way is the ego from the NPC? Cut towards it.
        self.side = "right" if lateral_offset(ctx.npc, ctx.ego) > 0 else "left"
        if ctx.npc_controller:
            ctx.npc_controller.blend_target_side = self.side
            ctx.npc_controller.blend = 0.0
        return {
            "side": self.side,
            "duration_seconds": self.duration,
            "gap_m": round(longitudinal_gap(ctx.ego, ctx.npc), 2),
        }

    def update(self, ctx: ScenarioContext) -> bool:
        if self.finished:
            return True
        self.elapsed += ctx.dt
        progress = min(self.elapsed / self.duration, 1.0)
        if ctx.npc_controller:
            ctx.npc_controller.blend = progress
        if progress >= 1.0:
            self.finished = True
            # Release the blend. By now the NPC's own lane *is* the ego's lane,
            # so leaving it set would steer it towards the next lane over and
            # it would keep drifting sideways across the carriageway.
            if ctx.npc_controller:
                ctx.npc_controller.blend = 0.0
                ctx.npc_controller.blend_target_side = None
        return self.finished


# --- runner --------------------------------------------------------------
class ScenarioRunner:
    """Owns the scenario vehicle, the background traffic and the event log."""

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)

        self.trigger = self._build(TRIGGERS, config.trigger, "trigger")
        self.action = self._build(ACTIONS, config.action, "action")

        self.npc: Optional[carla.Vehicle] = None
        self.npc_controller: Optional[LaneFollower] = None
        self.traffic: list[carla.Vehicle] = []

        self.events: list[dict[str, Any]] = []
        self.triggered = False
        self.triggered_at: Optional[float] = None

    @staticmethod
    def _build(registry: dict, config: dict, kind: str):
        if not config:
            return None
        type_name = config.get("type")
        if type_name not in registry:
            raise ValueError(
                f"unknown {kind} type {type_name!r} "
                f"(known: {', '.join(sorted(registry)) or 'none'})"
            )
        return registry[type_name](config)

    def log_event(self, sim_time: float, event_type: str, data: dict | None = None) -> None:
        self.events.append({
            "time": round(sim_time, 3),
            "type": event_type,
            "data": data or {},
        })

    # -- setup ---------------------------------------------------------
    def setup(self, world: carla.World, ego: carla.Vehicle) -> None:
        self._spawn_scenario_vehicle(world, ego)
        self._spawn_traffic(world, ego)

    def _spawn_scenario_vehicle(self, world: carla.World, ego: carla.Vehicle) -> None:
        spec = self.config.scenario_vehicle
        if not spec:
            return

        world_map = world.get_map()
        ego_wp = world_map.get_waypoint(
            ego.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving
        )
        if ego_wp is None:
            raise RuntimeError("ego is not on a driving lane; cannot place the NPC")

        side = spec.get("relative_lane", "left")
        lane_wp = ego_wp.get_left_lane() if side == "left" else ego_wp.get_right_lane()
        if lane_wp is None or lane_wp.lane_type != carla.LaneType.Driving:
            raise RuntimeError(f"no drivable {side} lane beside the ego spawn point")
        # Neighbouring lane must run the same way; sign of lane_id encodes it.
        if lane_wp.lane_id * ego_wp.lane_id < 0:
            raise RuntimeError(f"the {side} lane is oncoming traffic, not a parallel lane")

        offset = float(spec.get("initial_longitudinal_distance_m", 25.0))
        candidates = lane_wp.next(offset) if offset >= 0 else lane_wp.previous(-offset)
        if not candidates:
            raise RuntimeError("not enough road to place the scenario vehicle")
        target = candidates[0]

        transform = carla.Transform(
            carla.Location(
                x=target.transform.location.x,
                y=target.transform.location.y,
                z=target.transform.location.z + 0.3,  # avoid spawning in the tarmac
            ),
            target.transform.rotation,
        )

        blueprint = world.get_blueprint_library().find(
            spec.get("blueprint", "vehicle.audi.tt")
        )
        blueprint.set_attribute("role_name", "scenario")
        npc = world.try_spawn_actor(blueprint, transform)
        if npc is None:
            raise RuntimeError("could not spawn the scenario vehicle (spot occupied)")

        self.npc = npc
        self.npc_controller = LaneFollower(
            npc, world_map, target_speed_mps=float(spec.get("speed_mps", 17.0))
        )

    def _spawn_traffic(self, world: carla.World, ego: carla.Vehicle) -> None:
        count = int(self.config.traffic.get("vehicles", 0))
        if count <= 0:
            return

        keep_clear = float(self.config.traffic.get("keep_clear_m", 60.0))
        blueprints = world.get_blueprint_library().filter("vehicle.*")
        spawn_points = world.get_map().get_spawn_points()
        self.rng.shuffle(spawn_points)

        ego_location = ego.get_location()
        spawned = 0
        for point in spawn_points:
            if spawned >= count:
                break
            # Keep background traffic away from the manoeuvre under test.
            if point.location.distance(ego_location) < keep_clear:
                continue
            blueprint = blueprints[self.rng.randrange(len(blueprints))]
            vehicle = world.try_spawn_actor(blueprint, point)
            if vehicle is None:
                continue
            vehicle.set_autopilot(True)
            self.traffic.append(vehicle)
            spawned += 1

    # -- per tick ------------------------------------------------------
    def tick(self, ctx: ScenarioContext) -> None:
        if self.npc_controller is not None:
            self.npc_controller.step()

        if not self.triggered:
            if self.trigger is not None and self.trigger.check(ctx):
                self.triggered = True
                self.triggered_at = ctx.sim_time
                data = self.action.start(ctx) if self.action else {}
                self.log_event(ctx.sim_time, "CUT_IN_TRIGGERED", data)
        elif self.action is not None and not self.action.finished:
            if self.action.update(ctx):
                self.log_event(ctx.sim_time, "CUT_IN_COMPLETED", {
                    "gap_m": round(longitudinal_gap(ctx.ego, ctx.npc), 2)
                    if ctx.npc else None,
                })

    def npc_state(self, ego: carla.Vehicle) -> dict[str, Any]:
        if self.npc is None:
            return {}
        return {
            "npc_gap_m": round(longitudinal_gap(ego, self.npc), 3),
            "npc_lateral_m": round(lateral_offset(ego, self.npc), 3),
            "npc_speed_mps": round(speed_of(self.npc), 3),
        }

    # -- teardown ------------------------------------------------------
    def actors(self) -> list[carla.Actor]:
        return ([self.npc] if self.npc else []) + list(self.traffic)
