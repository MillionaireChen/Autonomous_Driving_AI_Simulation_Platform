#!/usr/bin/env python3
"""Collect an imitation-learning dataset by driving with the PID expert.

The simulator is also a data generator (spec section 43). Each episode records
the front camera plus the expert's control, and every sample is one row in a
JSONL index alongside its JPEG.

Variety matters more than volume here. A dataset collected from one spawn point
in one weather teaches a model to memorise one stretch of road, so episodes
sweep spawn points, weather and seeds.

Usage:
    uv run python scripts/collect_dataset.py --episodes 40
    uv run python scripts/collect_dataset.py --episodes 4 --duration 20   # smoke
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models.pid.policy import PIDAgent  # noqa: E402
from simulator import config as cfg  # noqa: E402
from simulator.scenario import load_scenario  # noqa: E402
from simulator.worker import SimulationWorker  # noqa: E402

# Spawn points on straight, multi-lane stretches of Town04 with a
# same-direction lane alongside, found by probing the map.
SPAWN_POINTS = [38, 39, 18, 19, 15, 16, 34, 35, 36, 40, 46, 47]

WEATHERS = [
    {"cloudiness": 10.0, "precipitation": 0.0, "sun_altitude_angle": 60.0},
    {"cloudiness": 60.0, "precipitation": 0.0, "sun_altitude_angle": 25.0},
    {"cloudiness": 80.0, "precipitation": 40.0, "sun_altitude_angle": 40.0},
    {"cloudiness": 30.0, "precipitation": 0.0, "sun_altitude_angle": 85.0},
    {"cloudiness": 90.0, "precipitation": 70.0, "sun_altitude_angle": 15.0},
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=40)
    parser.add_argument("--duration", type=float, default=40.0)
    parser.add_argument("--out", default=str(REPO_ROOT / "dataset" / "town04_pid"))
    parser.add_argument("--scenario", default="highway_cut_in")
    parser.add_argument("--start-index", type=int, default=0)
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    sim_config = cfg.load_simulator_config()
    camera_config = cfg.load_camera_config()
    ego_config = cfg.load_yaml("simulator/ego.yaml")
    episode_config = cfg.load_episode_config()
    evaluation_config = cfg.load_yaml("evaluation.yaml")

    worker = SimulationWorker(sim_config, camera_config, ego_config, episode_config,
                              evaluation_config=evaluation_config)

    total_samples = 0
    started = time.time()

    for i in range(args.start_index, args.start_index + args.episodes):
        rng = random.Random(1000 + i)
        scenario = load_scenario(args.scenario)
        scenario.seed = 1000 + i
        scenario.duration_seconds = args.duration
        scenario.ego["spawn_index"] = SPAWN_POINTS[i % len(SPAWN_POINTS)]
        scenario.weather = WEATHERS[i % len(WEATHERS)]
        # Vary the manoeuvre too, so the model does not learn one cut-in.
        scenario.scenario_vehicle["initial_longitudinal_distance_m"] = \
            -rng.uniform(15.0, 40.0)
        scenario.scenario_vehicle["speed_mps"] = rng.uniform(18.0, 24.0)
        scenario.action["speed_after_mps"] = rng.uniform(5.0, 11.0)

        episode_id = f"DS-{i:04d}"
        episode_dir = out_root / episode_id
        try:
            result = worker.run_episode(
                PIDAgent(), episode_id=episode_id,
                output_dir=episode_dir, scenario=scenario,
                record_frames=True,
            )
        except Exception as exc:
            print(f"{episode_id}: FAILED ({type(exc).__name__}: {exc})", flush=True)
            continue

        # Only keep episodes the expert actually drove well. Imitating a
        # crash teaches crashing.
        if result.collisions > 0 or result.result != "PASS":
            print(f"{episode_id}: discarded "
                  f"(result={result.result}, collisions={result.collisions})",
                  flush=True)
            continue

        samples = write_samples(episode_dir, episode_id, scenario)
        total_samples += samples
        print(f"{episode_id}: spawn={scenario.ego['spawn_index']} "
              f"score={result.score:.1f} samples={samples} "
              f"total={total_samples}", flush=True)

    elapsed = time.time() - started
    print(f"\n{total_samples} samples in {elapsed / 60:.1f} min -> {out_root}")
    return 0


def write_samples(episode_dir: Path, episode_id: str, scenario) -> int:
    """Join telemetry to recorded frames and append rows to the episode index.

    Only ticks with a frame become samples; a 20 Hz telemetry stream against a
    10 Hz camera means half of them.
    """
    telemetry_file = episode_dir / "telemetry.jsonl"
    frames_file = episode_dir / "frames.json"
    if not telemetry_file.exists() or not frames_file.exists():
        return 0

    frames = {f["tick"]: f for f in json.loads(frames_file.read_text())}
    rows = []
    with telemetry_file.open() as fh:
        for line in fh:
            tick = json.loads(line)
            frame = frames.get(tick["tick"])
            if frame is None:
                continue
            rows.append({
                "episode": episode_id,
                "image": str(Path(episode_id) / frame["path"]),
                "sim_time": tick["sim_time"],
                "speed_mps": tick["speed_mps"],
                "steer": tick["steer"],
                "throttle": tick["throttle"],
                "brake": tick["brake"],
                "route_command": "LANE_FOLLOW",
                "spawn_index": scenario.ego.get("spawn_index"),
            })

    (episode_dir / "samples.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else "")
    )
    return len(rows)


if __name__ == "__main__":
    sys.exit(main())
