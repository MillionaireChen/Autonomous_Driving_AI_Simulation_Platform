# Phase 4 - Scenario Engine

Goal: give the model something to react to, defined in YAML rather than code.

**Status: complete and passing.** Acceptance was "the cut-in triggers on every
run"; it fires at 6.05 s with a 12.15 m gap, identically, run after run.

## What was built

| Path | Purpose |
|---|---|
| `simulator/scenario.py` | Scenario config, trigger/action registries, runner |
| `simulator/npc.py` | `LaneFollower` - pure-pursuit lane keeping for scenario vehicles |
| `simulator/sensors.py` | `CollisionSensor` added, for the termination condition |
| `scenarios/highway_cut_in.yaml` | The Highway Cut-In scenario |
| `tests/test_scenario.py` | 25 tests covering geometry, triggers, actions, config |

## Design decisions

### Nothing in the engine knows what a cut-in is

`ScenarioRunner` reads a `trigger.type` and an `action.type` out of YAML and
looks them up in a registry. It has no branch for "highway cut-in"
(spec section 84.9). A new scenario is a new YAML file; a new *kind* of
scenario is one new `Trigger` or `ScenarioAction` subclass and a decorator.

Shipped so far: `relative_distance` and `elapsed_time` triggers, `cut_in`
action.

### The lane change is steered, not scripted

`CutInAction` does not move the NPC along a path. It ramps a lateral blend
from 0 to 1 on the NPC's steering target, interpolating between its own lane
and the target lane. The car steers, and its own dynamics decide the rest, so
the manoeuvre stays physical and its aggressiveness follows from
`duration_seconds`.

### Scenario NPCs cannot use autopilot

CARLA's traffic manager will not perform a specific manoeuvre at a specific
moment, which is the entire purpose of a scenario vehicle. So it gets an
explicit `LaneFollower`: pure pursuit for steering, proportional control on
speed. It is not a good driver; it is a repeatable one. Background traffic,
which only needs to be plausible, still uses autopilot.

## Two bugs worth recording

Both were found by looking at the telemetry rather than the pass/fail line,
and both would have produced a scenario that *looked* like it worked.

### The trigger fired at the wrong moment

Spec section 25 describes the NPC starting 25 m **ahead** at 17 m/s while the
ego does 15 m/s, cutting in at a 12 m relative distance. Those numbers cannot
produce a cut-in: a faster vehicle that is already ahead only pulls away, so
the gap never shrinks to 12 m and the trigger never fires.

The manoeuvre being described is an overtake-then-cut-in, so the scenario
starts the NPC **behind** the ego. That inverts the comparison: the gap now
grows through zero, and a "within 12 m" test is satisfied the instant the NPC
draws level - so it cut in across the ego's bonnet at a gap of **0.17 m**
instead of in front of it at 12 m.

`comparison` is now explicit in the YAML and has no safe default across both
directions:

- `at_most` - the NPC is closing from ahead and has come within range.
- `at_least` - the NPC is overtaking from behind and has built up a lead.

After the fix the trigger fires at a 12.15 m gap.

### The NPC kept drifting sideways after the manoeuvre

The lateral blend was left at 1.0 once the cut-in completed. But by then the
NPC's *own* lane is the ego's lane, so a blend of 1.0 towards "right" now
means the lane right of *that* one. It kept steering across the carriageway:
lateral offset went 0.01 m -> +6.54 m and onwards.

The action now releases the blend on completion. Measured after the fix, the
NPC settles at 0.13-0.35 m from the ego's lane centre and stays there.

## Acceptance results

```
episode EP-CUTIN-002: policy=dummy duration=40s sim=20Hz inference=10Hz
scenario highway_cut_in_001 (Highway Cut-In) map=Town04 seed=42

status              COMPLETED (COLLISION)
cut-in triggered    YES at 6.05s
collisions          1
ticks               672 (33.6s simulated in 23.1s wall)
distance            253.6 m
```

```
{"time": 0.00, "type": "EPISODE_STARTED",   "data": {"scenario": "highway_cut_in_001", "seed": 42}}
{"time": 6.05, "type": "CUT_IN_TRIGGERED",  "data": {"side": "right", "gap_m": 12.15}}
{"time": 8.05, "type": "CUT_IN_COMPLETED",  "data": {"gap_m": 30.7}}
{"time": 33.55,"type": "COLLISION",         "data": {"other_actor": "static.guardrail"}}
```

The manoeuvre itself, from the per-tick telemetry (lateral: -3.5 m is the left
lane, 0 is the ego's lane):

| t (s) | gap (m) | lateral (m) | |
|---|---|---|---|
| 0.00 | -26.4 | -6.5 | NPC starts behind, in the left lane |
| 3.00 | -17.8 | -3.8 | overtaking |
| 6.05 | +12.6 | -3.5 | **trigger fires** |
| 7.00 | +21.6 | -3.2 | steering across |
| 8.05 | +31.1 | -1.8 | cut-in completes |
| 11.00 | +56.3 | +0.1 | settled in the ego's lane |

**Repeatability**: three consecutive runs gave an identical trigger time
(6.05 s), gap (12.15 m), tick count (672) and distance (253.6 m).

**69 unit tests pass** with no CARLA running, 25 of them new: heading-relative
geometry, both trigger comparisons, the overtaking-vehicle case that caused
the first bug, blend release that caused the second, and config validation.

## Known issues

1. **The ego crashes into the guardrail at 33.6 s.** DummyAgent steers a
   constant 0, so it leaves the road where Town04 starts to bend. The scenario
   terminates on collision as configured. This is DummyAgent doing its job as
   a negative control (spec section 79), not a scenario fault - but it means
   the cut-in is currently observed rather than *responded to*. A model that
   actually reacts needs the PID baseline from Phase 10.
2. **`npc_lateral_m` is only meaningful nearby.** It is measured relative to
   the ego's heading, so once the NPC is 100 m ahead on a curve it reads as
   tens of metres off-axis without having changed lanes. Fine around the
   manoeuvre, misleading far from it.
3. **Background traffic is configured to 0.** The engine supports it, with a
   `keep_clear_m` radius so it cannot block the manoeuvre, but the shipped
   scenario runs without it so the acceptance result isolates the cut-in.
   Spec section 24 asks for 12; that is worth turning on once a model can
   actually drive the road.
4. **`route_completed` termination is not implemented.** Collision and timeout
   are. Route completion needs a route, which arrives with the evaluation
   engine in Phase 5.

## Not in this phase

Scoring. The episode records what happened - collision, trigger time, speeds -
but computes no TTC and no score. That is Phase 5.
