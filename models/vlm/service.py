#!/usr/bin/env python3
"""Serve the VLM decision policy over gRPC.

    uv run python models/vlm/service.py --port 51004 --device cuda:1
    uv run python models/vlm/service.py --port 51005 --device cuda:2 \
        --adapter output/federated/client_a
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from model_gateway.server import serve  # noqa: E402
from models.vlm.policy import DEFAULT_MODEL, VLMDecisionAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=51004)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter", default=None,
                        help="LoRA adapter directory; omit for the base model")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--model-id", default="vlm",
                        help="id the simulator registers this as")
    parser.add_argument("--client-id", default="")
    args = parser.parse_args()

    policy = VLMDecisionAgent(
        model_id=args.model, adapter=args.adapter,
        device=args.device, client_id=args.client_id,
    )
    print(f"loaded {args.model}"
          f"{' + ' + args.adapter if args.adapter else ' (base weights)'}"
          f" on {args.device}", flush=True)

    serve(policy, port=args.port, model_id=args.model_id,
          model_name="VLM Decision" + (f" ({args.client_id})" if args.client_id else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
