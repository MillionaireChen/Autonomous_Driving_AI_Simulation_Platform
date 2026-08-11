# Phase 3 - Model Gateway

Goal: get the model out of the simulator.

```
Simulation Worker -> gRPC -> Model Service -> gRPC -> Simulation Worker
```

**Status: complete and passing.** The same DummyAgent that ran in-process in
Phase 2 now runs as a separate process and drives the car over gRPC, with the
worker unchanged.

## What was built

| Path | Purpose |
|---|---|
| `model_gateway/protocol/driving.proto` | The service contract |
| `model_gateway/protocol/driving_pb2*.py` | Generated stubs (committed, regenerate with `make proto`) |
| `model_gateway/server.py` | Serve any `DrivingPolicy` over gRPC |
| `model_gateway/adapters/remote.py` | `RemoteModelAdapter` - a remote model as a local policy |
| `models/dummy/service.py` | DummyAgent as a standalone service |
| `configs/models.yaml` | Model registry (spec section 16) |
| `tests/test_model_gateway.py` | 18 tests against a real gRPC server |

The service exposes the four calls from spec section 15: `HealthCheck`,
`GetModelInfo`, `ResetEpisode`, `Infer`.

## Design decisions

### The adapter is a DrivingPolicy

`RemoteModelAdapter` implements the same interface as an in-process model, so
`SimulationWorker` was not modified to support remote models at all. The proof
is in the numbers below: the same episode produces the same distance either
way.

This only worked because Phase 2 kept `simulator/types.py` and
`simulator/policy.py` free of CARLA imports. The boundary was already in the
right place.

### The model declares what it needs

`GetModelInfo` returns `required_sensors`, and the adapter serialises only
those (spec section 49). A model that ignores the camera never receives one.

That is not a micro-optimisation. The front camera is 800x450x3 = 1.08 MB per
frame; at 10 Hz that is 10.8 MB/s of pointless traffic for a model that does
not look at pixels, and it becomes real money once the model is on another
host. Measured: with `required_sensors=()` the dummy model's episode sent
zero image bytes.

### A slow model fails; it does not stall the simulation

Every `Infer` carries a deadline, 500 ms by default (spec section 50). Missing
it raises `ModelTimeout`, which the worker treats as a failed inference and
answers with the safety fallback. Timeouts are counted separately from other
failures, because a model that is slow and a model that is wrong are different
problems.

Reset gets a deliberately longer budget: loading weights is legitimately slower
than inference.

### Generated stubs are committed

`driving_pb2.py` and friends are in the repo so a clone runs without protoc
installed. `make proto` regenerates them.

## Acceptance results

The same 20-second episode, the only difference being where the model lives:

| | in-process | over gRPC |
|---|---|---|
| status | COMPLETED | COMPLETED |
| ticks | 400 | 400 |
| distance | 88.5 m | **88.5 m** |
| avg / max speed | 4.4 / 8.3 m/s | 4.4 / 8.3 m/s |
| inferences | 200 | 200 |
| latency p50 | 0.005 ms | 1.473 ms |
| latency p95 | 0.007 ms | 3.207 ms |

Identical trajectory, ~1.5 ms of transport. That is the result Phase 3 was for:
the process boundary costs latency and nothing else.

Verified separately, with a purpose-built model that needs the camera and
misses its deadline every fourth call:

```
model_id         slow-camera
type             CONTROL_POLICY
required_sensors ('rgb_front',)
health_check     True

status           COMPLETED
inferences       60
model_timeouts   15          <- exactly every 4th call
invalid_actions  15          <- each timeout fell back safely
latency p50/p95  7.9 / 505.5 ms   <- the 500 ms deadline is what cut it off
```

- **Images cross the boundary intact.** All 60 inferences arrived at the model
  as `(450, 800, 3)` arrays; none were `None`.
- **The episode survived 15 timeouts** and still completed.
- **44 unit tests pass** with no CARLA running, 18 of them against a real gRPC
  server on a loopback port: codec round-trips, pose and scalar fidelity,
  health check, model info, reset propagation, sensor gating in both
  directions, payload shrinkage, and deadline enforcement.

## Known issues

1. **Raw `rgb8` is the default on the wire.** Fine on loopback; for a model on
   another host, `image_encoding="jpeg"` is supported and much smaller. There
   is no automatic choice yet.
2. **No retry or reconnect.** If the model service dies mid-episode, every
   remaining inference fails into the safety fallback and the car brakes to a
   stop. That is safe, but a real deployment wants reconnection.
3. **`ResetEpisode` is not enforced.** The worker calls it, but nothing stops a
   model from carrying state across episodes. Determinism checks in a later
   phase will need to.
4. **TRAJECTORY_POLICY is named but not implemented.** The enum value exists
   because model type is part of the handshake; the trajectory message and the
   controller that executes it belong to the phase that can actually drive one.

## Not in this phase

Scenarios are still nonexistent - the car drives down an empty highway until it
meets a barrier. Phase 4 introduces the YAML scenario engine and the Highway
Cut-In that gives the model something to react to.
