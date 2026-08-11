#!/usr/bin/env python3
"""Run DummyAgent as a standalone gRPC model service.

This is the reference for what "adding your own model" looks like: write a
DrivingPolicy, hand it to serve(), and the simulator can drive with it.

Usage:
    uv run python models/dummy/service.py --port 51001
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from model_gateway.server import serve  # noqa: E402
from models.dummy.policy import DummyAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=51001)
    parser.add_argument("--throttle", type=float, default=0.4)
    parser.add_argument("--steer", type=float, default=0.0)
    parser.add_argument("--brake", type=float, default=0.0)
    args = parser.parse_args()

    serve(
        DummyAgent(throttle=args.throttle, steer=args.steer, brake=args.brake),
        port=args.port,
        model_id="dummy",
        model_name="Dummy Constant Control",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
