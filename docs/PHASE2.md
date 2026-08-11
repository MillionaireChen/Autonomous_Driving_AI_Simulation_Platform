# Phase 2 - Simulation Worker

Goal: the closed loop itself.

```
observation -> policy -> action -> world tick -> next observation
```

**Status: complete and passing.** Acceptance was "at least one 20-second
closed-loop episode"; the worker runs 400 ticks of simulated time driven by a
policy, repeatably.

## What was built

| Path | Purpose |
|---|---|
| `simulator/types.py` | `Observation`, `VehicleControlAction`, `Pose`, `EpisodeResult` |
| `simulator/policy.py` | The `DrivingPolicy` interface: `reset` / `infer` / `close` |
| `simulator/worker.py` | The episode loop, clock, safety envelope and telemetry |
| `simulator/carla_client.py` | Connection and synchronous-world context manager |
| `simulator/ego.py` | Ego spawning, pose / speed / longitudinal acceleration |
| `simulator/sensors.py` | Camera attachment and non-blocking frame collection |
| `models/dummy/policy.py` | `DummyAgent`, the constant-control baseline |
| `scripts/run_episode.py` | Entry point for one episode |
| `configs/simulator/{ego,episode}.yaml` | Ego and episode parameters |
| `tests/test_types.py` | 26 unit tests, no CARLA required |

## Design decisions

### The policy cannot reach the simulator

`simulator/types.py` and `simulator/policy.py` import no CARLA. A policy
receives an `Observation` and returns a `VehicleControlAction`; it has no
handle on the world, the vehicle or the client (spec sections 73 / 84.10).

This is not tidiness for its own sake. It is the precondition for Phase 3:
moving a model behind gRPC should not require the worker to change, and it
cannot if the model was never able to touch CARLA in the first place.

### Two clocks, deliberately

The world steps at a fixed 20 Hz. The policy is asked at 10 Hz, and the
previous action is reapplied on the ticks in between (spec section 17). A real
vehicle behaves the same way between controller updates.

Measured on a 20 s episode: 400 ticks, exactly 200 inferences. The ratio is
derived from `fixed_delta_seconds` and `inference_hz`, not hard-coded.

### The simulator validates, the model proposes

Everything a policy returns is checked before it reaches the vehicle
(spec sections 50 / 73):

1. `is_finite()` first - NaN and inf are **rejected**, not clamped, because
   `min`/`max` propagate NaN silently and a clamped NaN is still NaN.
2. Then `clamped()` into throttle 0..1, steer -1..1, brake 0..1.
3. A policy that raises is caught and treated as a failed inference.

On rejection the vehicle gets the safety fallback: no throttle, brake 0.5, and
the wheel held where it was rather than snapped straight.

### Sensors are polled, not awaited

A 10 Hz camera against a 20 Hz world produces nothing on half the ticks.
Blocking on `queue.get()` would deadlock there, so `CameraSensor.poll()` drains
without blocking and carries the most recent frame forward.

## Acceptance results

```
episode EP-0001: policy=dummy duration=20s sim=20Hz inference=10Hz

status              COMPLETED
map                 Carla/Maps/Town04
ticks               400 (20.0s simulated in 18.8s wall)
distance            88.5 m
speed               avg 4.4 m/s, max 8.3 m/s
camera frames       208
inferences          200 (invalid 0)
inference latency   p50 0.005 ms, p95 0.007 ms
```

Verified beyond the run itself:

- **The loop closes.** 400 ticks, 200 inferences, 208 camera frames. Telemetry
  has one row per tick; the ego travels x=225.3 -> x=313.6.
- **Repeatable.** Separate runs give identical distance (88.5 m), average speed
  and peak speed.
- **Faster than real time.** 20.0 s simulated in 18.8 s wall, including world
  reload, on a shared GPU.
- **The safety envelope actually fires.** A deliberately broken policy that
  cycles NaN -> out-of-range -> raised exception was run for 5 s: 50 inferences,
  33 rejected. That is exactly the 17 NaN plus 16 exceptions, while the 17
  out-of-range actions were clamped rather than counted as failures. The
  episode still completed and the vehicle stayed put under the fallback brake.
- **26 unit tests pass** with no CARLA running, covering clamping, NaN
  rejection, the fallback and the ordering trap in point 1 above.

## Known issues

1. **DummyAgent crashes, by design.** With `steer=0` it drives straight into
   the Town04 barrier and stalls around 14 s. That is the point of it
   (spec section 79) and it is why the episode's average speed is well below
   its peak. There is no collision sensor yet to record the impact - that
   arrives with the evaluation engine in Phase 5.
2. **The world reloads every episode**, costing a few seconds. Fine at one
   episode at a time; batch evaluation in Phase 14 will want to reuse a world.
3. **Inference latency is meaningless right now** - microseconds, because the
   policy is a constant in-process. It becomes a real measurement in Phase 3
   once the model is behind gRPC.

## Not in this phase

The policy still runs in-process. Moving it behind a gRPC model gateway is
Phase 3, which is what makes the number in the latency field mean something.
