"""TCP (NeurIPS 2022) as a DrivingPolicy, using the Bench2Drive weights.

TCP is Trajectory-guided Control Prediction: a ResNet-34 over a wide forward
image plus a small measurement vector, with two heads - a trajectory branch
and a control branch. The Bench2Drive checkpoint is a student of the
Think2Drive RL teacher, and the published closed-loop numbers use the
trajectory branch, so that is what drives here.

Nothing about this file is inside the simulator. It runs in its own process,
behind gRPC, and the simulator neither imports it nor knows what a ResNet is.

Two things are copied from the upstream agent rather than invented, because
getting them wrong means feeding the network a world it never trained on:

* the three-camera crop and the 256x900 stitch (`_wide_image`), and
* the JPEG quality-20 round trip. That looks like a bug and is not: the
  Bench2Drive agent compresses every frame this hard before inference, so the
  weights have only ever seen images with those artefacts.
"""

from __future__ import annotations

import io
import math
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image as PILImage

from simulator.policy import DrivingPolicy
from simulator.types import Observation, VehicleControlAction

#: Copied from the Bench2Drive agent. Each view is cropped before stitching so
#: the overlapping parts of the three 70-degree frusta are not counted twice.
_CROPS = {
    "cam_front_left": (None, 1400),
    "cam_front": (200, 1400),
    "cam_front_right": (200, None),
}
#: Left to right, which is the order the panorama is built in.
_ORDER = ("cam_front_left", "cam_front", "cam_front_right")

_INPUT_HW = (256, 900)
_IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
_IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)

#: The agent divides speed by 12 before it reaches the network.
_SPEED_SCALE = 12.0
#: Bench2Drive uses six navigation commands where nuScenes-era models use
#: three. CARLA numbers them from 1, the network indexes from 0.
_COMMAND_INDEX = {
    "LEFT": 0, "RIGHT": 1, "STRAIGHT": 2,
    "LANE_FOLLOW": 3, "CHANGE_LANE_LEFT": 4, "CHANGE_LANE_RIGHT": 5,
}
#: `RoutePlanner(4.0, 50.0)` upstream: the target point is the first route
#: point more than this far away, so it does not collapse onto the bumper.
_TARGET_MIN_DIST_M = 4.0


class TCPAgent(DrivingPolicy):
    name = "tcp"
    model_type = "CONTROL_POLICY"
    #: The three views TCP stitches, plus the route it steers towards. No
    #: lead_vehicle: TCP is not given privileged ground truth, it has to see
    #: the car in front.
    required_sensors = (
        "cam_front", "cam_front_left", "cam_front_right", "speed", "route",
    )

    def __init__(
        self,
        checkpoint: str,
        repo_dir: str,
        device: str = "cuda:0",
        jpeg_quality: int = 20,
    ) -> None:
        # The upstream package is not installable, so it joins the path rather
        # than being vendored: keeping it as an untouched checkout is what
        # makes "these are the published weights, unmodified" checkable.
        repo = str(Path(repo_dir).resolve())
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from TCP.config import GlobalConfig
        from TCP.model import TCP

        self.device = torch.device(device)
        self.jpeg_quality = jpeg_quality
        self.config = GlobalConfig()

        net = TCP(self.config)
        raw = torch.load(checkpoint, map_location="cpu", weights_only=True)
        state = raw["state_dict"] if "state_dict" in raw else raw
        # Lightning prefixes everything with "model."
        stripped = OrderedDict(
            (key[6:] if key.startswith("model.") else key, value)
            for key, value in state.items()
        )
        missing, unexpected = net.load_state_dict(stripped, strict=False)
        if missing or unexpected:
            raise RuntimeError(
                f"TCP checkpoint does not match the architecture: "
                f"{len(missing)} missing, {len(unexpected)} unexpected"
            )

        self.net = net.to(self.device).eval()
        self._mean = _IMAGENET_MEAN.to(self.device)
        self._std = _IMAGENET_STD.to(self.device)
        self.last_waypoints: Optional[np.ndarray] = None

    # -- DrivingPolicy ----------------------------------------------------
    def reset(self, config: dict[str, Any]) -> None:
        # control_pid carries PID state in two deques. Left alone they would
        # leak the end of one episode into the start of the next, which is
        # exactly the kind of cross-episode coupling that makes a benchmark
        # non-reproducible.
        from TCP.model import PIDController

        cfg = self.config
        self.net.turn_controller = PIDController(
            K_P=cfg.turn_KP, K_I=cfg.turn_KI, K_D=cfg.turn_KD, n=cfg.turn_n
        )
        self.net.speed_controller = PIDController(
            K_P=cfg.speed_KP, K_I=cfg.speed_KI, K_D=cfg.speed_KD, n=cfg.speed_n
        )
        self.last_waypoints = None

        # Burn the CUDA autotune pass now rather than inside the first tick,
        # where it would land as a timeout against the model's budget.
        with torch.inference_mode():
            image = torch.zeros(1, 3, *_INPUT_HW, device=self.device)
            target = torch.zeros(1, 2, device=self.device)
            state = torch.zeros(1, 9, device=self.device)
            self.net(image, state, target)

    def infer(self, observation: Observation) -> VehicleControlAction:
        image = self._wide_image(observation)
        target_point = self._target_point(observation)

        speed = torch.tensor(
            [[observation.speed_mps / _SPEED_SCALE]],
            dtype=torch.float32, device=self.device,
        )
        target = torch.tensor(
            [target_point], dtype=torch.float32, device=self.device
        )
        command = torch.zeros(1, 6, dtype=torch.float32, device=self.device)
        command[0, self._command_index(observation.route_command)] = 1.0
        state = torch.cat([speed, target, command], dim=1)

        velocity = torch.tensor(
            [observation.speed_mps], dtype=torch.float32, device=self.device
        )

        with torch.inference_mode():
            prediction = self.net(image, state, target)
            steer, throttle, brake, _ = self.net.control_pid(
                prediction["pred_wp"], velocity, target
            )

        self.last_waypoints = prediction["pred_wp"][0].detach().cpu().numpy()

        # max_throttle is the ceiling the training data was collected under;
        # going above it would be extrapolating past every example TCP saw.
        return VehicleControlAction(
            steer=float(np.clip(float(steer), -1.0, 1.0)),
            throttle=float(np.clip(float(throttle), 0.0, self.config.max_throttle)),
            brake=float(np.clip(float(brake), 0.0, 1.0)),
        )

    # -- input assembly ---------------------------------------------------
    def _wide_image(self, observation: Observation) -> torch.Tensor:
        views = []
        for name in _ORDER:
            frame = observation.cameras.get(name)
            if frame is None:
                raise RuntimeError(f"TCP requires camera {name!r}, which was not sent")
            start, end = _CROPS[name]
            views.append(self._recompress(frame)[:, start:end, :])

        panorama = np.concatenate(views, axis=1)
        tensor = (
            torch.from_numpy(np.ascontiguousarray(panorama))
            .permute(2, 0, 1)
            .unsqueeze(0)
            .float()
        )
        tensor = F.interpolate(
            tensor, size=_INPUT_HW, mode="bilinear", align_corners=False
        )
        # Upstream rounds back to bytes here before normalising; keeping that
        # step means the tensor the network sees is bit-comparable to theirs.
        tensor = tensor.squeeze(0).byte().float().div_(255.0).to(self.device)
        return ((tensor - self._mean) / self._std).unsqueeze(0)

    def _recompress(self, frame: np.ndarray) -> np.ndarray:
        """The quality-20 JPEG round trip the weights were trained through."""
        buffer = io.BytesIO()
        PILImage.fromarray(frame).save(
            buffer, format="JPEG", quality=self.jpeg_quality
        )
        buffer.seek(0)
        return np.asarray(PILImage.open(buffer).convert("RGB"))

    @staticmethod
    def _target_point(observation: Observation) -> tuple[float, float]:
        """The next route point, rotated into the ego frame.

        Forward is +x and right is +y, which is the convention TCP was trained
        on. Derived from the ego yaw rather than a compass because the two are
        the same quantity and the simulator already publishes the yaw.
        """
        pose = observation.ego_pose
        chosen = None
        for x, y in observation.route_waypoints:
            if math.hypot(x - pose.x, y - pose.y) > _TARGET_MIN_DIST_M:
                chosen = (x, y)
                break
        if chosen is None:
            # No route left to aim at - the far end of the episode. Straight
            # ahead is the honest answer; inventing a turn would not be.
            return (_TARGET_MIN_DIST_M, 0.0)

        theta = math.radians(pose.yaw)
        dx, dy = chosen[0] - pose.x, chosen[1] - pose.y
        return (
            math.cos(theta) * dx + math.sin(theta) * dy,
            -math.sin(theta) * dx + math.cos(theta) * dy,
        )

    @staticmethod
    def _command_index(route_command: Optional[str]) -> int:
        return _COMMAND_INDEX.get((route_command or "").upper(), _COMMAND_INDEX["LANE_FOLLOW"])
