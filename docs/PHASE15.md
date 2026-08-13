# Phase 15 - A published model drives, and the registry learns to forget

v2.0 starts here. The v1 line-up - a constant-throttle agent, a hand-tuned PID
and a 23k-sample behaviour-cloning CNN - measured this platform rather than the
state of the art. This phase replaces it with a model from the literature and
builds the two pieces of plumbing that were in the way.

## What was built

**TCP (NeurIPS 2022) drives closed loop**, using the Bench2Drive checkpoint
unmodified. It is a ResNet-34 over a wide forward image plus a nine-element
measurement vector, with a trajectory head and a control head; the published
closed-loop numbers use the trajectory head, so that is what drives here.

**Multi-camera observations.** `Observation.cameras` is a name -> frame map and
the protocol carries `map<string, Image>`. The single-camera path is untouched:
`rgb_front` still exists, and every model built on it sees exactly what it saw
before.

**A retired model stops being offered without being deleted.**

## The CARLA version problem, and why it did not need solving

Three targets, three incompatible servers:

| | CARLA |
|---|---|
| this project | 0.9.16 |
| Bench2Drive / Bench2DriveZoo (TCP, UniAD, VAD, BEVFormer) | 0.9.15 |
| LMDrive | 0.9.10.1 |

None of it mattered. TCP consumes sensors and emits control; it never talks to
CARLA. The version those repos pin is a property of *their evaluation
scaffolding*, not of the weights. Wrapped as a gRPC service behind the existing
`DrivingPolicy` contract, TCP runs against 0.9.16 with **no new dependencies at
all** - the venv's torch 2.11.0+cu128 and torchvision 0.26.0 were enough.

This is the Phase 3 model gateway finally earning its keep.

## Three things that had to be got right

**TCP is not a single-camera model.** `TCP/config.py` sets `ignore_sides` and
`ignore_rear`, which reads like a front-camera model and is not one. The
Bench2Drive agent mounts three forward cameras at yaw -55 / 0 / +55, crops each,
concatenates them into a 4000x900 strip and resizes that to 256x900. Feeding it
one view would have been feeding it a world it never trained on.

**The default gRPC frame is 4 MB.** Three 1600x900 views are 12.96 MB raw, so
the first surround-view request would have died on `RESOURCE_EXHAUSTED`. Extra
cameras now always go as JPEG regardless of the front camera's encoding, and
both ends carry a 64 MB limit from `model_gateway/__init__.py`.

**The quality-20 JPEG round trip is not a bug.** The upstream agent compresses
every frame that hard before inference, so the weights have only ever seen
images with those artefacts. `TCPAgent._recompress` reproduces it deliberately.

## Acceptance

Checkpoint, loaded against a freshly built network:

```
$ curl -sL -o tcp_b2d.ckpt https://huggingface.co/rethinklab/Bench2DriveZoo/resolve/main/tcp_b2d.ckpt
305M  tcp_b2d.ckpt
tensors: 288
params: 26.6 M
missing   : 0 []
unexpected: 0 []
pred_wp   : (1, 4, 2)
```

Closed loop, same scenario and seed for both models:

```
$ uv run python scripts/run_episode.py --model tcp --scenario highway_cut_in
model requires sensors: cam_front, cam_front_left, cam_front_right, speed, route
extra cameras mounted: cam_front, cam_front_left, cam_front_right

status              COMPLETED (COLLISION)
cut-in triggered    YES at 7.40s
collisions          1
ticks               776 (38.8s simulated in 76.8s wall)
distance            264.1 m
speed               avg 6.8 m/s, max 9.7 m/s
inferences          388 (invalid 0, timeouts 0)
inference latency   p50 150.243 ms, p95 170.057 ms
lane invasions      0
route completion    42.7%
RESULT              FAIL   score 0.0 / 100
```

| | TCP | VLM (Qwen2.5-VL-7B) |
|---|---|---|
| result | FAIL 0.0 | PASS 98.3 |
| distance / route | 264.1 m / 42.7% | 565.0 m / 91.5% |
| collisions | 1 | 0 |
| lane invasions | 0 | 0 |
| inferences | 388 (0 invalid, 0 timeouts) | 400 (0 invalid, 0 timeouts) |
| latency p50 / p95 | 150.2 / 170.1 ms | 419.5 / 444.8 ms |

**Neither number means anything yet**, and the episode says so itself:

```
CUT_IN_COMPLETED  gap_m=32.04  entered_ego_lane=false  lateral_m=-1.94
SCENARIO_MANOEUVRE_FAILED  reason=scenario vehicle never entered the ego lane
COLLISION  other_actor=vehicle.audi.tt  intensity=3182.58
```

The VLM's 98.3 has `minimum TTC = no vehicle ahead`: the scenario car never
arrived, so it drove an empty motorway end to end. TCP hit that same car
because it drives slowly - 6.8 m/s against a 15 m/s target - and was still
alongside when the NPC ended up straddling the lane line at -1.94 m. The bug is
Phase 16's first job; until it is fixed the arena ranks nothing.

Zero lane invasions over 264 m is the evidence that TCP is genuinely steering.
Fed random noise it brakes flat out, which is the right answer to an
unreadable scene but tells you nothing - only real frames separate a working
model from a degenerate one.

## Registry

```
INFO  [alembic.runtime.migration] Running upgrade 0003 -> 0004, Retire a model
```

```
id       name                         archived
tcp      TCP (Bench2Drive)            False
vlm      VLM Decision (zero-shot)     False
pid      PID Lane Follower            True
cnn_il   CNN Imitation Learning       True
dummy    Dummy Constant Control       True

experiments: 219   (dummy 73, pid 70, cnn_il 69, tcp 4, vlm 3)
```

Deleting the rows was never an option: `experiments.model_id` is a foreign key
with no `ondelete`, so a `DELETE` fails against all 212 historical runs, and
forcing it through would destroy the evaluation history - the one artefact here
that cannot be regenerated. Removing an entry from `configs/models.yaml` now
archives its row instead, and `/api/models` stops listing it.

```
$ make test
152 passed in 2.96s
```

## Known issues

1. **The cut-in manoeuvre still does not happen.** Carried over from v1 and now
   the blocker for every comparison. `LaneFollower` falls back to its own lane
   when the target lane does not resolve; the fix is to target the ego's lane
   explicitly rather than whichever lane sits to the NPC's right.
2. **`episode.json` writes `sim_time: 0.00` for every event.** The console
   prints the right times, so the loss is in the export path only.
3. **`scripts/capture_dashboard.py` shoots early.** It waits on `text=/PASS|FAIL/`,
   which now matches the `SCENARIO_MANOEUVRE_FAILED` line in the timeline, so it
   captured a still-RUNNING episode at 16.6 s.
4. **`models/dummy/` and `models/il/` are dead code**, registered nowhere.
   `models/pid/` is not: `scripts/collect_dataset.py` still uses `PIDAgent` as
   its expert, and `simulator/lowlevel.py`'s `LaneKeepingController` is what
   executes the VLM's decisions. Deleting the first two means also fixing the
   module-level imports at `scripts/run_episode.py:25-26`, which otherwise break
   the script for every model.
5. **One policy instance serves four gRPC threads** with no session id in
   `InferRequest`. Two experiments sharing an endpoint would interleave on the
   same instance. Not hit yet because episodes run one model at a time.
6. **UniAD and VAD need calibrated cameras.** TCP only stitches pixels, so it
   needs no intrinsics; they project between frames and will need `cam_intrinsic`
   and `lidar2img`, which no structure in the repo currently carries.

## Environment

Only the uv venv now lives on local disk. `node`, `playwright`, `fe-stage` and
`next-build` moved to `/home/23R9802_Chen/v2/env/`, which took `/` from 97% full
(6.9 G free) to 88% (25 G free). PGDATA stays on local disk on purpose:
PostgreSQL documents corruption risk on NFS when locking is unreliable, and at
40 MB it was the smallest possible win for the largest possible risk.

Everything for this phase runs on **GPU 3**, which is shared - 27 GB of it
belongs to somebody else's job, leaving about 70 GB. The VLM's p50 rose from
101 ms in v1 to 419 ms here purely from that contention.
