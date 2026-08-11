# Autonomous Driving AI Arena

A GPU-accelerated closed-loop simulation platform for testing autonomous driving
models against interactive traffic scenarios using CARLA.

CARLA supplies the 3D world, vehicle physics, traffic and sensors. This project
supplies everything around it: the simulation orchestrator, a scenario engine
driven by YAML, a model adapter layer that keeps the simulator ignorant of what
a "driving model" actually is, a deterministic evaluation engine, and a web
dashboard to watch it all happen.

The core loop the platform is built around:

```
Observation_t  ->  Driving Policy  ->  Action_t  ->  CARLA  ->  Observation_t+1
```

## Status

**Phase 1 of 14 - CARLA Foundation.** Only the foundation is built so far: a
headless CARLA server pinned to a single GPU, a Python 3.11 client, and an
end-to-end smoke test that spawns an ego vehicle, streams camera frames, drives
it and stops it.

The simulation worker, model gateway, scenario engine, evaluation engine,
backend and dashboard land in later phases. Nothing in this repo is a
placeholder: if a directory exists, the code in it runs.

## Requirements

| | |
|---|---|
| OS | Ubuntu 20.04+ (verified on 20.04.6, glibc 2.31) |
| GPU | NVIDIA with Vulkan support (verified on RTX PRO 6000 Blackwell, driver 575.57) |
| Python | 3.11 (the CARLA wheel is `manylinux_2_31`, cp310-cp312) |
| CARLA | 0.9.16 packaged release, ~8 GB download / ~18 GB unpacked |
| Tooling | [uv](https://github.com/astral-sh/uv) |

You do **not** need Unreal Engine, an Epic Games account, or a CARLA source
build. The packaged release ships a precompiled UE4 runtime.

Docker is not required either. This host has no Docker access, so everything
runs bare-metal; see [docs/PHASE1.md](docs/PHASE1.md).

## Quick Start

```bash
cp .env.example .env      # adjust CARLA_ROOT / CARLA_GPU for your machine
make env                  # create the uv venv and install dependencies
make install-carla        # download and unpack the CARLA server
make carla-start          # start CARLA headless on the configured GPU
make smoke                # Phase 1 acceptance test
make carla-stop
```

`make smoke` connects to the server, loads Town04, spawns a Tesla Model 3,
attaches the front RGB camera, saves 20 frames, drives the car under throttle,
brakes it to a standstill and cleans up every actor. It exits non-zero if any
of those steps fails its numeric threshold.

## Layout

```
configs/
  simulator/carla.yaml       server connection, map, fixed timestep, GPU
  sensors/front_camera.yaml  800x450 @ 10 FPS front RGB camera
scripts/
  install_carla.sh           fetch and unpack the CARLA server package
  carla_server.sh            start / stop / status for the headless server
  carla_smoke_test.py        Phase 1 acceptance test
docs/
  PHASE1.md                  environment findings and acceptance results
```

Sensor and simulator parameters are read from YAML, never hard-coded in the
client. Scenarios follow the same rule from Phase 4 onward.

## Design Rules

These hold for every phase:

- The simulator never imports a specific neural network. Models arrive through
  a model adapter.
- Evaluation is deterministic. Collision, TTC and score are computed
  arithmetically, never judged by a language model.
- Scenario parameters live in YAML, not in Python.
- Every process declares which GPU it may use.
- Integration tests talk to a real CARLA server. Mocks are for unit tests only.

## License

Not yet specified.
