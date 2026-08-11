# Phase 5 - Evaluation Engine

Goal: turn an episode into numbers and a verdict.

**Status: complete and passing.** Acceptance was "the episode produces
metrics.json"; it does, with a deterministic score.

## What was built

| Path | Purpose |
|---|---|
| `simulator/metrics.py` | `EvaluationEngine` - accumulates per-tick state, scores the episode |
| `simulator/route.py` | Route generation and arc-length progress |
| `simulator/sensors.py` | `LaneInvasionSensor` added |
| `configs/evaluation.yaml` | Thresholds and scoring weights |
| `tests/test_metrics.py` | 32 tests: scoring arithmetic, TTC, comfort, route |

Metrics recorded (spec section 30): collision count, minimum TTC, TTC warning
and dangerous tick counts, lane invasions, route completion, distance, average
and peak speed, peak deceleration, peak lateral acceleration, peak jerk, hard
brake count, inference latency p50/p95/p99, model timeouts, duration, score
and PASS/FAIL.

## Design decisions

### Scoring is arithmetic, and the arithmetic is visible

Weights live in `configs/evaluation.yaml`; the engine sums them into a
`score_breakdown` that is written out alongside the total. Nothing is judged
and nothing is asked of a model (spec section 84.11). The tests assert exact
numbers: three lane invasions is exactly 70.0.

TTC bands do not stack - a 0.5 s minimum is charged 30, not 30 plus 15.

### TTC is undefined rather than large

A vehicle that is beside the ego, or pulling away, has no time-to-collision.
The engine returns `None` instead of a big number, because a big number
silently drags the episode minimum around and makes "no threat" look like
"distant threat".

A vehicle counts only when it is ahead, inside a 2 m lateral corridor, and
actually being closed on.

### A hard brake is an event, not a tick

Deceleration past -4 m/s² is counted once per braking event via an edge
detector. Counting per tick would charge a one-second brake at 20 Hz twenty
times, and the score penalty would be meaningless.

### An impact is not a comfort measurement

Hitting the guardrail registers about -157 m/s², 39 m/s² laterally and
3160 m/s³ of jerk. Left in, that swamps every comfort figure and charges the
score -3 for a "hard brake" that was actually a crash. Comfort accumulation
therefore stops once a collision has landed. The collision itself is still
recorded and still costs -100; it just does not masquerade as driving.

Before and after, same episode:

| | with impact | impact excluded |
|---|---|---|
| peak deceleration | -157.73 m/s² | **-0.96 m/s²** |
| peak lateral accel | 39.46 m/s² | **0.0098 m/s²** |
| hard brakes | 1 | **0** |

Zero hard brakes is the correct answer: DummyAgent never brakes at all.

### The scenario needed to be able to test braking

The first end-to-end run reported "no vehicle ahead" for TTC even though the
NPC had cut in front of the ego. That was correct: the NPC cuts in at 16 m/s
against an ego doing 7 m/s, so it is separating, and TTC is genuinely
undefined.

But it means the scenario could not test the thing spec section 25 says it is
for - whether the model slows down. The cut-in action now takes
`speed_after_mps`, and the NPC settles to 8 m/s once it is in the ego's lane.
TTC went from undefined to a real 4.55 s.

## Acceptance results

`output/episodes/EP-EVAL-003/metrics.json`, DummyAgent on the Highway Cut-In:

```json
{
  "collision_count": 1,
  "minimum_ttc_s": 4.548,
  "lane_invasion_count": 14,
  "route_completion_percent": 82.00,
  "distance_m": 253.58,
  "average_speed_mps": 7.55,
  "max_longitudinal_decel_mps2": -0.96,
  "max_lateral_acceleration_mps2": 0.0098,
  "hard_brake_count": 0,
  "episode_duration_s": 33.6,
  "score": 0.0,
  "result": "FAIL",
  "score_breakdown": {
    "base": 100.0, "collision": -100.0,
    "lane_invasion": -140.0, "route_incomplete": -3.5991
  }
}
```

DummyAgent failing is the expected result (spec section 79): it cannot steer,
so it wanders across 14 lane markings and hits a guardrail at 33.6 s having
covered 82% of the route.

**101 unit tests pass** with no CARLA running, 32 of them new.

## Known issues

1. **Jerk is a raw finite difference and is spiky.** CARLA's per-tick
   acceleration is noisy, and dividing its difference by a 0.05 s timestep
   amplifies that: the clean run still reports 348 m/s³. It needs low-pass
   filtering before it is a usable comfort number. Recorded, but not yet
   trustworthy, and deliberately not part of the score.
2. **Route completion is measured by nearest point.** A vehicle that turns
   around reports decreasing progress, which is honest, but a route that
   doubles back on itself would confuse it. Fine for a highway.
3. **`route_completed` is still not a termination condition** - it is a metric
   only. Collision and timeout end episodes.
4. **The score is dominated by lane invasions here** (-140 before flooring).
   That is spec section 36 applied literally. It is only visible because
   DummyAgent is a bad driver.

## Not in this phase

No database, no API, no dashboard. metrics.json on disk is the deliverable.
Phase 6 puts it behind FastAPI and PostgreSQL.
