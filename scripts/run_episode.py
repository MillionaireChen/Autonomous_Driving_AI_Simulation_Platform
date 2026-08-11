#!/usr/bin/env python3
"""Run one closed-loop episode.

    observation -> policy -> action -> CARLA -> next observation

The policy can be in-process or a remote gRPC model service. The simulation
worker cannot tell the difference; only the latency numbers change.

Usage:
    uv run python scripts/run_episode.py --policy dummy      # in-process
    uv run python scripts/run_episode.py --model dummy       # over gRPC
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

IN_PROCESS_POLICIES: dict[str, type[DrivingPolicy]] = {
    "dummy": DummyAgent,
}


def build_remote_policy(model_id: str, timeout_ms: float | None) -> DrivingPolicy:
    """Look the model up in configs/models.yaml and connect to it."""
    from model_gateway.adapters.remote import RemoteModelAdapter

    registry = cfg.load_yaml("models.yaml")
    entries = {m["id"]: m for m in registry.get("models", [])}
    if model_id not in entries:
        raise SystemExit(
            f"model {model_id!r} is not in configs/models.yaml "
            f"(known: {', '.join(sorted(entries)) or 'none'})"
        )
    entry = entries[model_id]
    return RemoteModelAdapter(
        endpoint=entry["endpoint"],
        timeout_ms=timeout_ms if timeout_ms is not None else entry.get("timeout_ms", 500),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--policy", choices=sorted(IN_PROCESS_POLICIES),
                        help="run a policy in this process")
    source.add_argument("--model", help="connect to a model from configs/models.yaml")
    parser.add_argument("--episode-id", default="EP-0001")
    parser.add_argument("--scenario", default=None,
                        help="scenario name or path, e.g. highway_cut_in")
    parser.add_argument("--duration", type=float, default=None,
                        help="override duration_seconds from episode.yaml")
    parser.add_argument("--model-timeout-ms", type=float, default=None)
    parser.add_argument("--no-eval", action="store_true",
                        help="skip scoring; just run the loop")
    parser.add_argument("--output", default=None,
                        help="directory for episode.json and telemetry.jsonl")
    args = parser.parse_args()

    if args.model:
        policy = build_remote_policy(args.model, args.model_timeout_ms)
        source_label = f"gRPC {policy.endpoint}"
    else:
        policy = IN_PROCESS_POLICIES[args.policy or "dummy"]()
        source_label = "in-process"

    sim_config = cfg.load_simulator_config()
    camera_config = cfg.load_camera_config()
    ego_config = cfg.load_yaml("simulator/ego.yaml")
    episode_config = cfg.load_episode_config()
    if args.duration is not None:
        episode_config["duration_seconds"] = args.duration

    output_dir = Path(args.output) if args.output else \
        REPO_ROOT / "output" / "episodes" / args.episode_id

    scenario = None
    if args.scenario:
        from simulator.scenario import load_scenario

        scenario = load_scenario(args.scenario)
        if args.duration is not None:
            scenario.duration_seconds = args.duration

    evaluation_config = None if args.no_eval else cfg.load_yaml("evaluation.yaml")
    worker = SimulationWorker(sim_config, camera_config, ego_config, episode_config,
                              evaluation_config=evaluation_config)

    duration = scenario.duration_seconds if scenario else episode_config["duration_seconds"]
    print(f"episode {args.episode_id}: policy={policy.name} ({source_label}) "
          f"duration={duration}s "
          f"sim={worker.sim_hz:.0f}Hz inference={worker.sim_hz / worker.inference_interval:.0f}Hz")
    if scenario:
        print(f"scenario {scenario.id} ({scenario.name}) map={scenario.map} seed={scenario.seed}")
    if policy.required_sensors:
        print(f"model requires sensors: {', '.join(policy.required_sensors)}")

    result = worker.run_episode(policy, episode_id=args.episode_id,
                                output_dir=output_dir, scenario=scenario)

    print(f"\nstatus              {result.status} ({result.termination_reason})")
    print(f"map                 {result.map_name}")
    if scenario:
        triggered = (f"YES at {result.scenario_triggered_at:.2f}s"
                     if result.scenario_triggered else "NO")
        print(f"cut-in triggered    {triggered}")
        print(f"collisions          {result.collisions}")
    print(f"ticks               {result.ticks} ({result.simulated_seconds:.1f}s simulated"
          f" in {result.wall_seconds:.1f}s wall)")
    print(f"distance            {result.distance_m:.1f} m")
    print(f"speed               avg {result.average_speed_mps:.1f} m/s,"
          f" max {result.max_speed_mps:.1f} m/s")
    print(f"camera frames       {result.camera_frames}")
    print(f"inferences          {result.inferences}"
          f" (invalid {result.invalid_actions}, timeouts {result.model_timeouts})")
    print(f"inference latency   p50 {result.inference_latency_ms_p50:.3f} ms,"
          f" p95 {result.inference_latency_ms_p95:.3f} ms")

    if result.result:
        ttc = ("%.2f s" % result.minimum_ttc_s) if result.minimum_ttc_s is not None \
            else "no vehicle ahead"
        print(f"\nminimum TTC         {ttc}")
        print(f"lane invasions      {result.lane_invasions}")
        print(f"route completion    {result.route_completion_percent:.1f}%")
        print(f"RESULT              {result.result}   score {result.score:.1f} / 100")

    print(f"\nartifacts           {output_dir}")

    return 0 if result.status == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
