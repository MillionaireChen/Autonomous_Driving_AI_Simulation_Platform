"""The learned driving policy, served like any other model.

Loads the trained checkpoint and answers with control. It also predicts a short
trajectory; that head is exposed through `last_waypoints` so a trajectory
controller can drive from it instead.

Note what this policy declares: `rgb_front` and `speed`, and nothing else. It
gets no route and no lead-vehicle ground truth - unlike the PID expert it was
trained from. It has to read the road out of the image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch
from PIL import Image

from models.il.dataset import DrivingDataset
from models.il.model import DrivingNet, normalise
from simulator.policy import DrivingPolicy
from simulator.types import (
    Observation, TrajectoryAction, TrajectoryPoint, VehicleControlAction,
)

#: Seconds ahead each predicted waypoint corresponds to, matching training.
HORIZONS_S = (0.5, 1.0, 1.5, 2.0)

DEFAULT_CHECKPOINT = Path(__file__).resolve().parent / "checkpoints" / "cnn_il.pt"


class CNNILAgent(DrivingPolicy):
    name = "cnn_il"
    required_sensors = ("rgb_front", "speed")

    def __init__(
        self,
        checkpoint: Path | str = DEFAULT_CHECKPOINT,
        device: Optional[str] = None,
        brake_threshold: float = 0.35,
        mode: str = "trajectory",
    ) -> None:
        self.device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        payload = torch.load(Path(checkpoint), map_location=self.device)
        self.model = DrivingNet(
            num_waypoints=payload.get("num_waypoints", 4), pretrained=False
        )
        self.model.load_state_dict(payload["state_dict"])
        self.model.to(self.device).eval()

        self.checkpoint_epoch = payload.get("epoch")
        self.validation_loss = payload.get("validation_loss")
        # Throttle and brake are both sigmoid outputs and nothing stops the
        # network asking for both at once. Below this the brake is treated as
        # off, so the car does not drive with the brake lightly on.
        self.brake_threshold = brake_threshold

        # "trajectory" drives from the predicted path; "control" from the
        # predicted pedals. The control head collapses on this data - steering
        # labels have a standard deviation of 0.009 on a near-straight
        # highway, so L1 loss is minimised by emitting the mean and the car
        # drifts into the barrier. The waypoint head carries real spatial
        # variance and does not degenerate the same way.
        if mode not in ("trajectory", "control"):
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.model_type = ("TRAJECTORY_POLICY" if mode == "trajectory"
                           else "CONTROL_POLICY")
        self.last_waypoints: list[tuple[float, float]] = []

    def reset(self, config: dict[str, Any]) -> None:
        self.last_waypoints = []
        # First CUDA inference includes kernel autotuning and took over the
        # 500 ms deadline, costing a timeout on tick 0. Burn it here, where
        # there is a longer budget and nothing is driving yet.
        with torch.inference_mode():
            dummy = torch.zeros(
                (1, 3, DrivingDataset.HEIGHT, DrivingDataset.WIDTH),
                device=self.device,
            )
            self.model(normalise(dummy),
                       torch.zeros((1, 1), device=self.device))

    @torch.inference_mode()
    def infer(self, observation: Observation) -> VehicleControlAction:
        if observation.rgb_front is None:
            # No image, no opinion. The worker's safety fallback is a better
            # answer than a guess from an empty tensor.
            raise RuntimeError("cnn_il requires rgb_front and received none")

        image = Image.fromarray(observation.rgb_front).resize(
            (DrivingDataset.WIDTH, DrivingDataset.HEIGHT), Image.BILINEAR
        )
        tensor = torch.from_numpy(
            np.asarray(image, dtype=np.float32) / 255.0
        ).permute(2, 0, 1).unsqueeze(0).to(self.device)
        speed = torch.tensor(
            [[observation.speed_mps / DrivingDataset.SPEED_SCALE]],
            dtype=torch.float32, device=self.device,
        )

        control, waypoints = self.model(normalise(tensor), speed)
        self.last_waypoints = [
            (float(x), float(y))
            for x, y in waypoints[0].view(-1, 2).tolist()
        ]

        if self.mode == "trajectory":
            return TrajectoryAction(waypoints=[
                TrajectoryPoint(x=x, y=y, timestamp_s=HORIZONS_S[i])
                for i, (x, y) in enumerate(self.last_waypoints)
                if i < len(HORIZONS_S)
            ])

        steer, throttle, brake = (float(v) for v in control[0].tolist())
        if brake < self.brake_threshold:
            brake = 0.0
        else:
            throttle = 0.0
        return VehicleControlAction(steer=steer, throttle=throttle, brake=brake)
