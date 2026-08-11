# Phase 1 - CARLA Foundation

Goal: prove the foundation works end to end on this machine. Connect to CARLA,
load Town04, spawn an ego vehicle, attach a front RGB camera, capture frames,
drive the car, stop it, and tear everything down without leaking state.

**Status: complete and passing.**

## Files added

| Path | Purpose |
|---|---|
| `scripts/install_carla.sh` | Fetch and unpack the CARLA 0.9.16 server package |
| `scripts/carla_server.sh` | start / stop / status for the headless server |
| `scripts/carla_smoke_test.py` | Phase 1 acceptance test |
| `configs/simulator/carla.yaml` | Host, port, map, fixed timestep, GPU |
| `configs/sensors/front_camera.yaml` | 800x450, fov 90, 10 FPS front RGB camera |
| `pyproject.toml`, `uv.lock`, `.python-version` | Pinned Python 3.11 environment |
| `Makefile`, `.env.example`, `README.md` | Entry points and configuration |

No `backend/`, `frontend/`, `simulator/` or `model_gateway/` package exists yet.
Those arrive with the phases that need them; empty scaffolding would be
placeholder code that cannot be run or tested.

## How this deployment differs from the original spec

The spec assumed `docker compose up`. This host cannot do that, so Phase 1 runs
bare-metal. Every deviation below is forced by a measured property of the
machine, not a preference.

### Docker is unavailable

The account is not in the `docker` group (only `dl` is), there is no
passwordless `sudo`, and no rootless alternative is installed - no `podman`, no
`apptainer`. Only `singularity 3.7.0` exists, which is old and would add a
container layer for no benefit here.

CARLA ships a self-contained package, so it runs directly. `docker-compose.yml`
is deliberately **not** written yet: an unrunnable compose file is a placeholder,
and it would claim a deployment path nobody has verified. It belongs to the
phase where Docker access exists.

### Storage is split across two filesystems

| Mount | Size | Free | Used for |
|---|---|---|---|
| `/` (local) | 208 G | **15 G** | uv venv, wheel cache, interpreter only |
| `/home` (NFS) | 128 T | 34 T | repo, CARLA server, all outputs |

Local disk is 93% full and shared with other users' work (24 G and 16 G
belonging to two colleagues). CARLA needs ~19 G unpacked, which does not fit,
so the server package lives on NFS. Nothing belonging to anyone else was moved
or deleted.

Only the Python environment is kept local, where import latency matters. The
interpreter, venv and uv cache all sit under `/var/tmp/fls`, set through `.env`.

### GPU allocation

Four RTX PRO 6000 Blackwell cards, three of them running other people's jobs
(50-64 GB each). CARLA is pinned to **GPU 0** through both
`CUDA_VISIBLE_DEVICES` and UE4's `-graphicsadapter`. Measured usage while
running: 1.7 GB on GPU 0, nothing on GPUs 1-3.

### Versions

| Component | Version |
|---|---|
| CARLA server + client | 0.9.16 (released 2025-09-16) |
| Python | 3.11.15 |
| OS / glibc | Ubuntu 20.04.6 / 2.31 |
| NVIDIA driver | 575.57.08 |

The CARLA wheel is published as `manylinux_2_31` for cp310-cp312, which matches
glibc 2.31 exactly, so 3.11 works on 20.04 despite 0.9.16 being a 2025 release.
Town04 ships in the base package; `AdditionalMaps` is not needed.

## Commands

```bash
cp .env.example .env
make env             # uv venv on local disk + dependencies
make install-carla   # ~8 GB download, ~19 GB unpacked, to NFS
make carla-start     # headless on GPU 0, RPC ready in ~11 s
make smoke           # acceptance test
make carla-stop
```

## Acceptance results

```
CARLA connection successful  client 0.9.16 / server 0.9.16
Map loaded                   Carla/Maps/Town04
Sync mode enabled            fixed_delta_seconds=0.05 (20 steps/s)
Vehicle spawned              vehicle.tesla.model3 id=397 spawn_point=0
Camera active                800x450 fov=90 @10Hz
Vehicle moved                displacement 24.1 m, peak speed 6.4 m/s
Vehicle stopped              final speed 0.00 m/s
20 frames received           saved to output/smoke_test/ (received 73 total)
Cleanup successful           actors destroyed, server back in async mode

  [PASS] frames_saved
  [PASS] frames_not_blank
  [PASS] vehicle_moved
  [PASS] vehicle_stopped

Phase 1 smoke test: PASS (12.6s)
```

Verified beyond the script's own verdict:

- **Frames are real renders.** 800x450, pixel std 54.3, full 0-255 range, and
  the images show the Town04 highway with guardrails and lane markings. Frame 1
  and frame 20 differ by 11.05 mean absolute pixel value, so the world is
  genuinely moving rather than a repeated still.
- **Deterministic.** Three consecutive runs produced identical displacement
  (24.1 m), peak speed (6.4 m/s) and frame count (73). Only the server-side
  actor id changes.
- **No leaks.** After the run: 0 vehicles, 0 sensors, `synchronous_mode=False`,
  and the server still answers new clients.
- **GPU released.** GPU 0 returns from 1.7 GB to 25 MiB after `carla-stop`.

## Known issues and gotchas

1. **Never set `SDL_VIDEODRIVER=offscreen`.** It is the obvious thing to reach
   for on a display-less host, but it makes CARLA 0.9.16 exit 1 immediately
   after printing `Disabling core dumps.` with no further diagnostics and no
   crash log. `-RenderOffScreen` alone is correct and sufficient. This cost the
   only real debugging detour in Phase 1.
2. **Synchronous mode is sticky.** If a client dies while the world is
   synchronous, the server stays that way and the next client appears to hang.
   The smoke test restores asynchronous mode in a `finally` block; any future
   client must do the same.
3. **`CarlaUE4.sh` is a wrapper, not an `exec`.** The real process is
   `CarlaUE4-Linux-Shipping` underneath it, and it can outlive the wrapper.
   `carla_server.sh stop` reaps it, matching on our own RPC port so a CARLA
   belonging to another user on this shared box is never touched.
4. **First boot off NFS.** The RPC port opens in ~11 s, which is acceptable.
   Asset streaming during heavier scenarios has not been stress-tested yet; if
   it becomes a bottleneck, the fix is local disk space, which is currently
   unavailable.
5. **No `node`/`npm` on this host.** The Next.js dashboard in Phase 7 will need
   a toolchain installed first.

## Not in this phase

Simulation worker, model gateway and gRPC protocol, scenario engine, evaluation
engine, backend, dashboard. Phase 1 stops here by design.
