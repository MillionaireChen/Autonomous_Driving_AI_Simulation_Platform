# Handoff - state of the build and what is left

This file is the contract for whoever continues the work, human or agent. Keep
it current: update the phase table and the notes at the bottom every time a
phase lands.

## Rules that do not change

1. **One phase at a time, one commit per phase.** Commit message says what was
   done and *why*, including anything surprising that was found.
2. **Author is `Chen Jinhua <Chen-Jinhua@sodick.co.jp>` only.** Never add a
   Claude/AI co-author line, never mention an assistant in a commit message.
3. **Push after every phase.** `git push origin main`.
4. **Nothing goes in that has not run.** No placeholder files, no empty
   directories, no scaffolding for a phase that has not arrived.
5. **Every phase gets `docs/PHASEn.md`** with: what was built, design
   decisions, real acceptance output pasted in, and known issues. Bugs found
   along the way are written down, not quietly fixed.
6. **Real logs in the README.** The `Verified Runs` section carries actual
   terminal output from this machine. Extend it, do not fabricate it.
7. **Evaluation stays deterministic arithmetic.** Never ask a model to judge a
   collision, a TTC or a score.
8. **Scenario and sensor parameters live in YAML**, never hard-coded.
9. **The simulator never imports a specific neural network.** Models arrive
   through the gRPC adapter.
10. **`make test` must stay green** and must not need CARLA.

## Environment

| | |
|---|---|
| Repo | `/home/23R9802_Chen/autonomous-driving-ai-arena` (NFS) |
| Python | `/var/tmp/fls/adarena/venv` (uv, local disk) |
| CARLA | `/home/23R9802_Chen/carla/CARLA_0.9.16` (NFS, 19 GB) |
| Node | `/var/tmp/fls/adarena/node/bin` (v22.13.1) |
| npm staging | `/var/tmp/fls/adarena/fe-stage` (local disk) |
| PostgreSQL | `pgserver` wheel, PGDATA `/var/tmp/fls/adarena/pgdata` (local disk) |
| Playwright | `PLAYWRIGHT_BROWSERS_PATH=/var/tmp/fls/adarena/playwright` |

**GPU 0 only.** GPUs 1-3 belong to other people's jobs; never touch them.
**Local disk `/` has ~17 GB free** and is shared with colleagues. Only runtimes
go there; everything the platform produces goes to `/home`.
**No Docker on this host** (not in the `docker` group, no sudo).

Bring the stack up:

```bash
cd /home/23R9802_Chen/autonomous-driving-ai-arena
./scripts/carla_server.sh start                      # ~12 s
uv run python models/dummy/service.py --port 51001 & # model service
uv run uvicorn backend.main:app --port 8000 &        # API + PostgreSQL
(cd frontend && ARENA_NEXT_DIST=/var/tmp/fls/adarena/next-build \
   PATH=/var/tmp/fls/adarena/node/bin:$PATH npm start &)
```

Tear down when finished: stop CARLA (`./scripts/carla_server.sh stop`), kill
the API/model/frontend processes, and stop PostgreSQL with
`.../pgserver/pginstall/bin/pg_ctl -D /var/tmp/fls/adarena/pgdata -m fast stop`.
Leaving CARLA running holds GPU 0 from other users.

## Phases

| # | Phase | Status |
|---|---|---|
| 1 | CARLA Foundation | done |
| 2 | Simulation Worker | done |
| 3 | Model Gateway (gRPC) | done |
| 4 | Scenario Engine | done |
| 5 | Evaluation Engine | done |
| 6 | Backend (API + PostgreSQL) | done |
| 7 | Frontend dashboard | done |
| 8 | Bird-Eye View | done |
| 9 | Replay | done |
| 10 | PID / rule-based baseline | done |
| 11 | Learned driving model | done |
| 12 | Model Arena (A vs B) | **next** |
| 13 | Parallel simulation (2 CARLA instances) | todo |
| 14 | Batch evaluation over seeds | todo |

### Phase 9 - Replay

Episodes already write `telemetry.jsonl`, `events.jsonl`, `episode.json` and
`metrics.json` to `output/experiments/<id>/`. Frames are **not** saved yet.

- Add a frame recorder to the worker (JPEG per camera frame, under
  `camera_front/`), off by default and enabled per-experiment - 336 frames per
  episode at 27 KB is ~9 MB, which is fine, but only when asked for.
- `GET /api/experiments/{id}/replay` serving the recorded telemetry/events, and
  a frame endpoint.
- A replay page in the dashboard: play, pause, scrub. Reuse `BirdEyeView`, it
  already takes a tick and a trail.
- Acceptance: a finished experiment replays with camera, telemetry, BEV and
  timeline in sync.

### Phase 10 - PID baseline

The important one: nothing can currently drive. DummyAgent crashes into the
guardrail, so the cut-in is observed but never *responded to*.

- Lane-following on CARLA waypoints plus a speed controller. `simulator/npc.py`
  already has a working pure-pursuit + P-speed `LaneFollower`; the ego version
  needs the same shape behind the `DrivingPolicy` interface, served over gRPC
  like the dummy.
- Add emergency braking on TTC, so the cut-in produces an actual brake.
- Register as `pid` in `configs/models.yaml`.
- Acceptance: completes Highway Cut-In with **no collision** and a score well
  above zero, and the timeline shows a brake in response to the cut-in. That
  contrast with DummyAgent is the demo (spec section 79).

### Phase 11 - Learned driving model

The user asked specifically for a *driving* model, not object detection.

- Collect a dataset with the PID expert (`dataset/`, on NFS): front camera,
  speed, route command, and the expert's control.
- Train an end-to-end policy: image encoder (ResNet18) + speed, predicting
  control. TCP-style dual output (waypoints *and* control) is preferred, since
  it exercises the trajectory path too.
- To take waypoint-output models, implement `TRAJECTORY_POLICY`: add the
  trajectory message to `driving.proto` and a trajectory-following controller
  in the simulator (spec sections 13/14).
- Register as `cnn_il`. Acceptance: closes the loop and beats DummyAgent.
- Note: published CARLA-leaderboard weights (Transfuser, TCP, InterFuser) are
  built against CARLA 0.9.10.1 sensor rigs. They will load and run here, but
  their published numbers will not reproduce without matching the rig - train
  locally instead, or document the gap honestly.

### Phases 12-14

12: run two models on the same scenario/seed and compare, with a comparison
page. 13: a second CARLA instance on another port and GPU (**GPU 0 is the only
free card - check `nvidia-smi` before assuming a second is available**).
14: N seeds per model/scenario, aggregate success rate, collision rate, mean
TTC and score.

## Known issues carried forward

- `hard_brake_count` on the `episodes` row is always 0; the real value is in
  `metrics.json` and the flat `metrics` table.
- Jerk is a raw finite difference of a noisy signal and reads ~348 m/s^3 on a
  clean run. Needs filtering before it means anything. Not in the score.
- `frames` table from spec section 38 is not created; it arrives with the
  Phase 9 recorder.
- Tests use SQLite, not PostgreSQL.
- Determinism is verified *within* a CARLA server instance (three identical
  runs). Across server restarts the cut-in trigger time has been seen to move
  by ~0.15 s. Worth pinning down in Phase 14, where it matters most.
- The dashboard status bar shows the current tick's TTC, so it reads `-` at the
  end of a run; the episode minimum is in the result panel.
