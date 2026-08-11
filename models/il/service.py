#!/usr/bin/env python3
"""Run the learned driving model as a gRPC service.

Usage:
    uv run python models/il/service.py --port 51003
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from model_gateway.server import serve  # noqa: E402
from models.il.policy import DEFAULT_CHECKPOINT, CNNILAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=51003)
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--device", default=None, help="cuda, cuda:1, cpu")
    parser.add_argument("--mode", default="trajectory", choices=["trajectory", "control"])
    args = parser.parse_args()

    policy = CNNILAgent(checkpoint=args.checkpoint, device=args.device, mode=args.mode)
    print(f"loaded {args.checkpoint} (epoch {policy.checkpoint_epoch}, "
          f"val loss {policy.validation_loss:.4f}) on {policy.device} "
          f"as {policy.model_type}", flush=True)

    serve(policy, port=args.port, model_id="cnn_il",
          model_name="CNN Imitation Learning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
