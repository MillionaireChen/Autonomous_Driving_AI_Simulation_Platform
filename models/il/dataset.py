"""Dataset for imitation learning from recorded episodes.

Reads the artefacts an episode already writes - `telemetry.jsonl`,
`frames.json` and the JPEGs - rather than a separate export. Every recorded run
is therefore training data, and nothing has to be re-collected when the label
set changes.

Two targets per sample:

* the expert's control at that instant, and
* where the expert actually went over the next two seconds, expressed in the
  ego's own frame.

The second is what makes this a driving model rather than a control regressor:
predicting a short trajectory forces the network to represent where the road
goes, and it is the output a trajectory-following controller can consume.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

#: Seconds ahead for the trajectory targets.
HORIZONS_S = (0.5, 1.0, 1.5, 2.0)


@dataclass
class Sample:
    image_path: Path
    speed_mps: float
    control: tuple[float, float, float]      # steer, throttle, brake
    waypoints: tuple[tuple[float, float], ...]


def to_ego_frame(x: float, y: float, yaw_deg: float,
                 tx: float, ty: float) -> tuple[float, float]:
    """World point (tx, ty) expressed in the frame of a pose at (x, y, yaw).

    Forward is +x, right is +y, which is CARLA's convention.
    """
    yaw = math.radians(yaw_deg)
    dx, dy = tx - x, ty - y
    return (dx * math.cos(yaw) + dy * math.sin(yaw),
            -dx * math.sin(yaw) + dy * math.cos(yaw))


def load_episode(episode_dir: Path,
                 horizons_s: Iterable[float] = HORIZONS_S) -> list[Sample]:
    telemetry_file = episode_dir / "telemetry.jsonl"
    frames_file = episode_dir / "frames.json"
    if not telemetry_file.exists() or not frames_file.exists():
        return []

    with telemetry_file.open() as fh:
        ticks = [json.loads(line) for line in fh]
    frames = {f["tick"]: f["path"] for f in json.loads(frames_file.read_text())}
    if not ticks:
        return []

    dt = 0.05
    if len(ticks) > 1:
        dt = max(1e-3, ticks[1]["sim_time"] - ticks[0]["sim_time"])
    offsets = [int(round(h / dt)) for h in horizons_s]

    samples: list[Sample] = []
    for index, tick in enumerate(ticks):
        path = frames.get(tick["tick"])
        if path is None:
            continue
        # Drop samples too close to the end to have a full horizon; a padded
        # trajectory target would teach the model to slow down at the end of
        # every episode.
        if index + offsets[-1] >= len(ticks):
            continue

        waypoints = tuple(
            to_ego_frame(tick["x"], tick["y"], tick.get("yaw", 0.0),
                         ticks[index + o]["x"], ticks[index + o]["y"])
            for o in offsets
        )
        samples.append(Sample(
            image_path=episode_dir / path,
            speed_mps=float(tick["speed_mps"]),
            control=(float(tick["steer"]), float(tick["throttle"]),
                     float(tick["brake"])),
            waypoints=waypoints,
        ))
    return samples


def load_dataset(root: Path) -> list[Sample]:
    samples: list[Sample] = []
    for episode_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        samples.extend(load_episode(episode_dir))
    return samples


class DrivingDataset(Dataset):
    """Image + speed -> control + short trajectory."""

    #: Input resolution. The camera is 800x450; a wide, short crop keeps the
    #: road and drops most of the sky.
    WIDTH, HEIGHT = 256, 144

    #: Speeds are divided by this so the scalar input sits near unit scale.
    SPEED_SCALE = 20.0

    def __init__(self, samples: list[Sample], augment: bool = False) -> None:
        self.samples = samples
        self.augment = augment

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int):
        sample = self.samples[index]
        image = Image.open(sample.image_path).convert("RGB")
        image = image.resize((self.WIDTH, self.HEIGHT), Image.BILINEAR)
        array = np.asarray(image, dtype=np.float32) / 255.0

        if self.augment:
            # Photometric only. A horizontal flip would need the steering sign
            # and the trajectory flipped too, and on a one-way carriageway it
            # produces scenes that cannot occur.
            array = np.clip(
                array * np.float32(np.random.uniform(0.8, 1.2))
                + np.float32(np.random.uniform(-0.06, 0.06)),
                0.0, 1.0,
            )

        tensor = torch.from_numpy(array).permute(2, 0, 1)
        return (
            tensor,
            torch.tensor([sample.speed_mps / self.SPEED_SCALE], dtype=torch.float32),
            torch.tensor(sample.control, dtype=torch.float32),
            torch.tensor(sample.waypoints, dtype=torch.float32).flatten(),
        )
