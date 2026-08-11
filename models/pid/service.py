#!/usr/bin/env python3
"""Run PIDAgent as a gRPC model service.

Usage:
    uv run python models/pid/service.py --port 51002
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from model_gateway.server import serve  # noqa: E402
from models.pid.policy import PIDAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=51002)
    parser.add_argument("--target-speed", type=float, default=15.0)
    args = parser.parse_args()

    serve(
        PIDAgent(target_speed_mps=args.target_speed),
        port=args.port,
        model_id="pid",
        model_name="PID Lane Follower",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
