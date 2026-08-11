#!/usr/bin/env python3
"""Run one closed-loop episode.

    observation -> policy -> action -> CARLA -> next observation

Phase 2 entry point. The policy still runs in-process here; Phase 3 moves it
behind gRPC without the worker changing.

Usage:
    uv run python scripts/run_episode.py
    uv run python scripts/run_episode.py --policy dummy --duration 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The repo is not pip-installed, so make its packages importable regardless of
# where this script is invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models.dummy.policy import DummyAgent  # noqa: E402
from simulator import config as cfg  # noqa: E402
from simulator.policy import DrivingPolicy  # noqa: E402
from simulator.worker import SimulationWorker  # noqa: E402

POLICIES: dict[str, type[DrivingPolicy]] = {
    "dummy": DummyAgent,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default="dummy", choices=sorted(POLICIES))
    parser.add_argument("--episode-id", default="EP-0001")
    parser.add_argument("--duration", type=float, default=None,
                        help="override duration_seconds from episode.yaml")
    parser.add_argument("--output", default=None,
                        help="directory for episode.json and telemetry.jsonl")
    args = parser.parse_args()

    sim_config = cfg.load_simulator_config()
    camera_config = cfg.load_camera_config()
    ego_config = cfg.load_yaml("simulator/ego.yaml")
    episode_config = cfg.load_episode_config()
    if args.duration is not None:
        episode_config["duration_seconds"] = args.duration

    output_dir = Path(args.output) if args.output else \
        REPO_ROOT / "output" / "episodes" / args.episode_id

    worker = SimulationWorker(sim_config, camera_config, ego_config, episode_config)
    policy = POLICIES[args.policy]()

    print(f"episode {args.episode_id}: policy={policy.name} "
          f"duration={episode_config['duration_seconds']}s "
          f"sim={worker.sim_hz:.0f}Hz inference={worker.sim_hz / worker.inference_interval:.0f}Hz")

    result = worker.run_episode(policy, episode_id=args.episode_id, output_dir=output_dir)

    print(f"\nstatus              {result.status}")
    print(f"map                 {result.map_name}")
    print(f"ticks               {result.ticks} ({result.simulated_seconds:.1f}s simulated"
          f" in {result.wall_seconds:.1f}s wall)")
    print(f"distance            {result.distance_m:.1f} m")
    print(f"speed               avg {result.average_speed_mps:.1f} m/s,"
          f" max {result.max_speed_mps:.1f} m/s")
    print(f"camera frames       {result.camera_frames}")
    print(f"inferences          {result.inferences}"
          f" (invalid {result.invalid_actions})")
    print(f"inference latency   p50 {result.inference_latency_ms_p50:.3f} ms,"
          f" p95 {result.inference_latency_ms_p95:.3f} ms")
    print(f"artifacts           {output_dir}")

    return 0 if result.status == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
