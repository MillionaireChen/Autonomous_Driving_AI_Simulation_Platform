#!/usr/bin/env python3
"""Measure what a VLM actually costs per decision, on real CARLA frames.

Everything about the design downstream depends on this number. A decision rate
of 2 Hz needs the median under about 400 ms; if it is not, the architecture has
to change rather than the prompt.

Uses recorded frames rather than a live episode, so it can run while CARLA is
busy and measures only the model.

Usage:
    uv run python scripts/probe_vlm_latency.py --model Qwen/Qwen2.5-VL-7B-Instruct
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import torch  # noqa: E402
from PIL import Image  # noqa: E402

DECISIONS = ("KEEP_LANE", "SLOW_DOWN", "BRAKE", "CHANGE_LEFT", "CHANGE_RIGHT")

PROMPT = (
    "You are the high-level decision module of a driving system on a highway.\n"
    "Speed: {speed:.1f} m/s. Lead vehicle: {lead}.\n"
    "Choose exactly one action and reply with only that word:\n"
    + ", ".join(DECISIONS)
)


def find_frames(limit: int) -> list[Path]:
    """Recorded camera frames from any episode that has them."""
    frames: list[Path] = []
    for root in (REPO_ROOT / "output" / "experiments",
                 REPO_ROOT / "dataset" / "town04_pid"):
        if not root.exists():
            continue
        for jpg in sorted(root.glob("*/camera_front/*.jpg")):
            frames.append(jpg)
            if len(frames) >= limit:
                return frames
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--device", default="cuda:1")
    parser.add_argument("--max-pixels", type=int, default=384 * 384,
                        help="cap the visual token count; the single biggest "
                             "latency lever on a VLM")
    args = parser.parse_args()

    frames = find_frames(args.frames)
    if not frames:
        raise SystemExit("no recorded frames found; run an episode with "
                         "record_frames first")
    print(f"{len(frames)} frames, model {args.model}, device {args.device}")

    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    started = time.time()
    processor = AutoProcessor.from_pretrained(args.model, max_pixels=args.max_pixels)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.model, dtype=torch.bfloat16, device_map=args.device,
    ).eval()
    print(f"loaded in {time.time() - started:.1f}s")

    latencies: list[float] = []
    replies: list[str] = []

    for index, frame in enumerate(frames):
        image = Image.open(frame).convert("RGB")
        text = PROMPT.format(speed=12.5, lead="18.0 m ahead, closing at 4.0 m/s")
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": text},
        ]}]

        prompt = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[prompt], images=[image],
                           return_tensors="pt").to(args.device)

        torch.cuda.synchronize(args.device)
        t0 = time.perf_counter()
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=6, do_sample=False)
        torch.cuda.synchronize(args.device)
        elapsed = (time.perf_counter() - t0) * 1000.0

        reply = processor.batch_decode(
            out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )[0].strip()

        # The first call includes kernel autotuning; it is not representative.
        if index == 0:
            print(f"  warmup: {elapsed:.0f} ms -> {reply!r}")
            continue
        latencies.append(elapsed)
        replies.append(reply)

    print(f"\nlatency over {len(latencies)} frames:")
    print(f"  median {statistics.median(latencies):.0f} ms")
    print(f"  mean   {statistics.mean(latencies):.0f} ms")
    print(f"  min    {min(latencies):.0f} ms   max {max(latencies):.0f} ms")
    usable = statistics.median(latencies)
    print(f"\n  -> {1000 / usable:.1f} Hz sustainable "
          f"({'2 Hz OK' if usable < 450 else 'too slow for 2 Hz'})")

    valid = sum(1 for r in replies if r.split()[0].strip('.,') in DECISIONS
                if r.split())
    print(f"\nreplies parsed as a known decision: {valid}/{len(replies)}")
    print("sample:", replies[:5])
    print(f"peak GPU memory: "
          f"{torch.cuda.max_memory_allocated(args.device) / 1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
