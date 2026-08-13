#!/usr/bin/env python3
"""Serve TCP (NeurIPS 2022) over gRPC, using the Bench2Drive weights.

    uv run python models/tcp/service.py --port 51005 --device cuda:3

The upstream checkout and the checkpoint live outside the repo and are passed
in, so nothing here depends on where they were unpacked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from model_gateway.server import serve  # noqa: E402
from models.tcp.policy import TCPAgent  # noqa: E402

DEFAULT_REPO = "/home/23R9802_Chen/v2/tcp-zoo"
DEFAULT_CKPT = "/home/23R9802_Chen/v2/ckpts/tcp_b2d.ckpt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=51005)
    parser.add_argument("--checkpoint", default=DEFAULT_CKPT)
    parser.add_argument("--repo-dir", default=DEFAULT_REPO,
                        help="Bench2DriveZoo checkout (tcp/admlp branch)")
    parser.add_argument("--device", default="cuda:3")
    parser.add_argument("--model-id", default="tcp",
                        help="id the simulator registers this as")
    args = parser.parse_args()

    policy = TCPAgent(
        checkpoint=args.checkpoint, repo_dir=args.repo_dir, device=args.device,
    )
    print(f"loaded TCP from {args.checkpoint} on {args.device}", flush=True)

    serve(policy, port=args.port, model_id=args.model_id,
          model_name="TCP (Bench2Drive)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
