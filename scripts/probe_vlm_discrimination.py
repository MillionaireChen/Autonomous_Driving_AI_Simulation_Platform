#!/usr/bin/env python3
"""Does the VLM actually discriminate, or does it just always say KEEP_LANE?

Phase 11 taught this the hard way: a model that emits one constant can look
excellent on an aggregate metric. Before anything is built on top of the VLM,
it has to be shown to change its answer when the situation changes.

Two things are varied independently:

* the **image** - a clear road versus the frame where the NPC is cutting in;
* the **text context** - no lead vehicle versus one very close and closing.

A useful decision module must respond to both. If it only responds to the text,
the camera is decoration and the whole VLM premise is weaker than it looks.

Usage:
    uv run python scripts/probe_vlm_discrimination.py --device cuda:1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import torch  # noqa: E402
from PIL import Image  # noqa: E402

DECISIONS = ("KEEP_LANE", "SLOW_DOWN", "BRAKE", "CHANGE_LEFT", "CHANGE_RIGHT")

PROMPT = (
    "You are the high-level decision module of a highway driving system.\n"
    "You see the forward camera. Current speed: {speed:.1f} m/s.\n"
    "Lead vehicle: {lead}\n\n"
    "Pick the single safest action. Reply with one word only, from:\n"
    "KEEP_LANE (road clear, hold speed)\n"
    "SLOW_DOWN (traffic ahead, ease off)\n"
    "BRAKE (imminent collision risk, brake hard)\n"
    "CHANGE_LEFT / CHANGE_RIGHT (lane blocked, move over)\n"
)

CONTEXTS = [
    ("clear", 14.0, "none detected"),
    ("far", 14.0, "32 m ahead, same speed"),
    ("closing", 14.0, "18 m ahead, closing at 5 m/s"),
    ("critical", 14.0, "6 m ahead, closing at 8 m/s"),
]


def pick_frames() -> dict[str, Path]:
    """One frame of clear road and one with the cut-in vehicle in view.

    EXP-0009 recorded the cut-in: around tick 120 the NPC is directly ahead,
    and early ticks are empty road.
    """
    root = REPO_ROOT / "output" / "experiments"
    frames: dict[str, Path] = {}
    for episode in sorted(root.glob("*/camera_front")):
        index = {int(p.stem): p for p in episode.glob("*.jpg")}
        if len(index) < 130:
            continue
        # frames.json maps ticks to files; index order is the recording order,
        # so early = start of episode, ~60 = around the cut-in at 6 s (10 Hz).
        frames["empty_road"] = index[min(index)]
        frames["cut_in"] = index[sorted(index)[62]]
        break
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--device", default="cuda:1")
    parser.add_argument("--max-pixels", type=int, default=384 * 384)
    args = parser.parse_args()

    frames = pick_frames()
    if len(frames) < 2:
        raise SystemExit("need a recorded episode with >=130 frames; "
                         "run one with record_frames=true")
    for name, path in frames.items():
        print(f"{name:12} {path}")

    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    processor = AutoProcessor.from_pretrained(args.model, max_pixels=args.max_pixels)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.model, dtype=torch.bfloat16, device_map=args.device,
    ).eval()

    def decide(image: Image.Image, speed: float, lead: str) -> str:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": PROMPT.format(speed=speed, lead=lead)},
        ]}]
        prompt = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[prompt], images=[image],
                          return_tensors="pt").to(args.device)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=6, do_sample=False)
        reply = processor.batch_decode(
            out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )[0].strip()
        return reply.split()[0].strip(".,*") if reply.split() else "<empty>"

    images = {name: Image.open(path).convert("RGB") for name, path in frames.items()}
    # Warm up; the first call is not representative.
    decide(next(iter(images.values())), 14.0, "none detected")

    print(f"\n{'':14}" + "".join(f"{name:>14}" for name, _, _ in CONTEXTS))
    results: dict[str, dict[str, str]] = {}
    for image_name, image in images.items():
        row = {}
        for context_name, speed, lead in CONTEXTS:
            row[context_name] = decide(image, speed, lead)
        results[image_name] = row
        print(f"{image_name:14}" + "".join(f"{row[c]:>14}" for c, _, _ in CONTEXTS))

    distinct = {v for row in results.values() for v in row.values()}
    print(f"\ndistinct decisions produced: {sorted(distinct)}")

    text_sensitive = any(
        len({row[c] for c, _, _ in CONTEXTS}) > 1 for row in results.values()
    )
    image_sensitive = any(
        len({results[i][c] for i in results}) > 1 for c, _, _ in CONTEXTS
    )
    print(f"responds to the text context: {text_sensitive}")
    print(f"responds to the image:        {image_sensitive}")
    if len(distinct) == 1:
        print("\nVERDICT: constant output - unusable as a decision module")
    elif not image_sensitive:
        print("\nVERDICT: text-driven only - the camera is decoration")
    else:
        print("\nVERDICT: discriminates on both")

    (REPO_ROOT / "output").mkdir(exist_ok=True)
    (REPO_ROOT / "output" / "vlm_discrimination.json").write_text(
        json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
