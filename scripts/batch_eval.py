#!/usr/bin/env python3
"""Run every model over N seeds of a scenario and print the leaderboard.

One episode is a sample of one. This is the honest version of "does it drive":
the same scenario, the same seeds, every model, and rates rather than anecdotes.

Requires the backend, the model services and at least one CARLA server.

Usage:
    uv run python scripts/batch_eval.py --models pid dummy --count 10
    uv run python scripts/batch_eval.py --models pid cnn_il dummy --count 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DONE = {"COMPLETED", "FAILED", "STOPPED"}


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}") as response:
        return json.load(response)


def post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--models", nargs="+", default=["pid", "dummy"])
    parser.add_argument("--scenario", default="highway_cut_in_001")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--out", default=str(REPO_ROOT / "output" / "batch"))
    args = parser.parse_args()

    started = time.time()
    batch = post(args.base, "/api/batch", {
        "model_ids": args.models,
        "scenario_id": args.scenario,
        "seed_start": args.seed_start,
        "count": args.count,
    })
    experiments = batch["experiments"]
    pool = get(args.base, "/api/simulators")
    print(f"{len(experiments)} episodes "
          f"({len(args.models)} models x {args.count} seeds) "
          f"across {pool['size']} simulator(s)")

    # Poll until every episode has reached a terminal state.
    while True:
        states = [get(args.base, f"/api/experiments/{e}")["status"]
                  for e in experiments]
        finished = sum(1 for s in states if s in DONE)
        running = sum(1 for s in states if s == "RUNNING")
        print(f"\r  {finished}/{len(experiments)} finished, {running} running, "
              f"{time.time() - started:.0f}s elapsed", end="", flush=True)
        if finished == len(experiments):
            break
        time.sleep(5)
    print()

    # Summarise exactly this batch: filtering by seed alone would pull in every
    # earlier experiment that happened to use the same seed.
    summary = get(args.base, f"/api/aggregate?scenario_id={args.scenario}"
                             f"&experiments={','.join(experiments)}")

    header = (f"{'model':<10}{'n':>4}{'success':>9}{'collision':>11}"
              f"{'mean':>8}{'p95':>7}{'worst':>7}{'meanTTC':>9}"
              f"{'route%':>8}{'lane':>6}{'p50 ms':>8}")
    print(f"\n{header}")
    print("-" * len(header))
    for row in summary["models"]:
        ttc = f"{row['mean_minimum_ttc']:.2f}" if row["mean_minimum_ttc"] else "n/a"
        print(f"{row['model_id']:<10}{row['episodes']:>4}"
              f"{row['success_rate'] * 100:>8.0f}%{row['collision_rate'] * 100:>10.0f}%"
              f"{row['mean_score']:>8.1f}{row['p95_score']:>7.1f}{row['worst_score']:>7.1f}"
              f"{ttc:>9}{row['mean_route_completion']:>8.1f}"
              f"{row['mean_lane_invasions']:>6.1f}{row['mean_latency_p50']:>8.2f}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "scenario_id": args.scenario,
        "models": args.models,
        "seeds": batch["seeds"],
        "experiments": experiments,
        "wall_seconds": round(time.time() - started, 1),
        "summary": summary["models"],
    }
    (out_dir / "leaderboard.json").write_text(json.dumps(report, indent=2))
    print(f"\n{len(experiments)} episodes in {(time.time() - started) / 60:.1f} min"
          f" -> {out_dir / 'leaderboard.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
