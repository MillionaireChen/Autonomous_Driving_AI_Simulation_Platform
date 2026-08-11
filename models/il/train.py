#!/usr/bin/env python3
"""Train the end-to-end driving model on expert episodes.

Usage:
    uv run python models/il/train.py --epochs 12
    uv run python models/il/train.py --epochs 1 --limit 2000   # smoke
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

from models.il.dataset import DrivingDataset, load_dataset  # noqa: E402
from models.il.model import DrivingNet, normalise  # noqa: E402


def split(samples, holdout: float = 0.15):
    """Hold out whole episodes, not random frames.

    Consecutive frames are nearly identical, so a random split leaks the
    validation set into training and the loss looks far better than the model
    is.
    """
    episodes = sorted({s.image_path.parent.parent.name for s in samples})
    cut = max(1, int(len(episodes) * holdout))
    validation_episodes = set(episodes[-cut:])
    train = [s for s in samples if s.image_path.parent.parent.name not in validation_episodes]
    validate = [s for s in samples if s.image_path.parent.parent.name in validation_episodes]
    return train, validate, sorted(validation_episodes)


def run_epoch(model, loader, device, optimiser=None, waypoint_weight: float = 0.4):
    training = optimiser is not None
    model.train(training)
    control_loss_fn = nn.L1Loss()
    waypoint_loss_fn = nn.L1Loss()

    totals = {"loss": 0.0, "control": 0.0, "waypoint": 0.0, "steer": 0.0, "n": 0}
    with torch.set_grad_enabled(training):
        for images, speeds, controls, waypoints in loader:
            images = normalise(images.to(device, non_blocking=True))
            speeds = speeds.to(device, non_blocking=True)
            controls = controls.to(device, non_blocking=True)
            waypoints = waypoints.to(device, non_blocking=True)

            predicted_control, predicted_waypoints = model(images, speeds)
            control_loss = control_loss_fn(predicted_control, controls)
            waypoint_loss = waypoint_loss_fn(predicted_waypoints, waypoints)
            loss = control_loss + waypoint_weight * waypoint_loss

            if training:
                optimiser.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                optimiser.step()

            batch = images.size(0)
            totals["loss"] += loss.item() * batch
            totals["control"] += control_loss.item() * batch
            totals["waypoint"] += waypoint_loss.item() * batch
            totals["steer"] += (predicted_control[:, 0] - controls[:, 0]).abs().sum().item()
            totals["n"] += batch

    n = max(1, totals["n"])
    return {k: v / n for k, v in totals.items() if k != "n"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(REPO_ROOT / "dataset" / "town04_pid"))
    parser.add_argument("--out", default=str(REPO_ROOT / "models" / "il" / "checkpoints"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="cap samples, for smoke runs")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}"
          f"{' (' + torch.cuda.get_device_name(0) + ')' if device.type == 'cuda' else ''}")

    samples = load_dataset(Path(args.data))
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        raise SystemExit(f"no samples under {args.data}")

    train_samples, validation_samples, held_out = split(samples)
    print(f"{len(samples)} samples: {len(train_samples)} train, "
          f"{len(validation_samples)} validation")
    print(f"held-out episodes: {', '.join(held_out)}")

    train_loader = DataLoader(
        DrivingDataset(train_samples, augment=True), batch_size=args.batch_size,
        shuffle=True, num_workers=args.workers, pin_memory=True, drop_last=True,
    )
    validation_loader = DataLoader(
        DrivingDataset(validation_samples), batch_size=args.batch_size,
        shuffle=False, num_workers=args.workers, pin_memory=True,
    )

    model = DrivingNet().to(device)
    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=args.epochs)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    history = []
    best = float("inf")
    started = time.time()

    for epoch in range(1, args.epochs + 1):
        train_metrics = run_epoch(model, train_loader, device, optimiser)
        validation_metrics = run_epoch(model, validation_loader, device)
        scheduler.step()

        history.append({"epoch": epoch, "train": train_metrics,
                        "validation": validation_metrics})
        print(f"epoch {epoch:2d}/{args.epochs}  "
              f"train {train_metrics['loss']:.4f} "
              f"(ctrl {train_metrics['control']:.4f}, wp {train_metrics['waypoint']:.4f})  "
              f"val {validation_metrics['loss']:.4f} "
              f"(ctrl {validation_metrics['control']:.4f}, "
              f"wp {validation_metrics['waypoint']:.4f}, "
              f"steer MAE {validation_metrics['steer']:.4f})", flush=True)

        if validation_metrics["loss"] < best:
            best = validation_metrics["loss"]
            torch.save({"state_dict": model.state_dict(),
                        "num_waypoints": model.num_waypoints,
                        "epoch": epoch,
                        "validation_loss": best},
                       out_dir / "cnn_il.pt")

    (out_dir / "history.json").write_text(json.dumps(history, indent=2))
    print(f"\nbest validation loss {best:.4f} after {(time.time() - started) / 60:.1f} min")
    print(f"checkpoint: {out_dir / 'cnn_il.pt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
