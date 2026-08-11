"""RemoteModelAdapter - talk to a model over gRPC as if it were local.

Implements DrivingPolicy, so the simulation worker cannot tell the difference
between this and an in-process model (spec section 49). Everything on the far
side of it may be on another GPU, in another container or on another host.

Two things this does beyond plumbing:

* It asks the model which sensors it needs and sends only those. A model that
  ignores camera data does not pay to move a megabyte of pixels every tick.
* It enforces a deadline. A model slower than the budget is a failed
  inference, not a stalled simulation (spec section 50).
"""

from __future__ import annotations

import io
from typing import Any, Optional

import grpc
import numpy as np

from model_gateway.protocol import driving_pb2 as pb
from model_gateway.protocol import driving_pb2_grpc as pb_grpc
from simulator.policy import DrivingPolicy
from simulator.types import (
    Observation, TrajectoryAction, TrajectoryPoint, VehicleControlAction,
)


class ModelTimeout(RuntimeError):
    """The model did not answer within its deadline."""


class ModelUnavailable(RuntimeError):
    """The model service could not be reached."""


def encode_image(array: np.ndarray, encoding: str) -> pb.Image:
    height, width = array.shape[:2]
    if encoding == "rgb8":
        data = np.ascontiguousarray(array, dtype=np.uint8).tobytes()
    elif encoding == "jpeg":
        from PIL import Image as PILImage

        buffer = io.BytesIO()
        PILImage.fromarray(array).save(buffer, format="JPEG", quality=90)
        data = buffer.getvalue()
    else:
        raise ValueError(f"unsupported image encoding: {encoding!r}")
    return pb.Image(width=width, height=height, encoding=encoding, data=data)


class RemoteModelAdapter(DrivingPolicy):
    def __init__(
        self,
        endpoint: str,
        timeout_ms: float = 500.0,
        image_encoding: str = "rgb8",
        connect_timeout_s: float = 10.0,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_ms / 1000.0
        self.image_encoding = image_encoding

        #: Counted separately from other failures so an episode can report how
        #: often the model missed its budget (spec section 50).
        self.timeouts = 0

        self._channel = grpc.insecure_channel(endpoint)
        try:
            grpc.channel_ready_future(self._channel).result(timeout=connect_timeout_s)
        except grpc.FutureTimeoutError as exc:
            raise ModelUnavailable(
                f"no model service at {endpoint} after {connect_timeout_s}s"
            ) from exc
        self._stub = pb_grpc.DrivingModelStub(self._channel)

        info = self._stub.GetModelInfo(pb.GetModelInfoRequest(), timeout=connect_timeout_s)
        self.model_id = info.id
        self.name = info.name or info.id
        self.version = info.version
        self.model_type = pb.ModelType.Name(info.type)
        self.required_sensors = tuple(info.required_sensors)

    # -- DrivingPolicy ----------------------------------------------------
    def reset(self, config: dict[str, Any]) -> None:
        response = self._stub.ResetEpisode(
            pb.ResetEpisodeRequest(
                episode_id=str(config.get("episode_id", "")),
                fixed_delta_seconds=float(config.get("fixed_delta_seconds") or 0.0),
                target_speed_mps=float(config.get("target_speed_mps") or 0.0),
                spawn_index=int(config.get("spawn_index") or 0),
            ),
            timeout=self.timeout_s * 10,  # a reset may legitimately load weights
        )
        if not response.ok:
            raise RuntimeError(f"model refused reset: {response.detail}")

    def infer(self, observation: Observation):
        request = pb.InferRequest(observation=self._to_proto(observation))
        try:
            response = self._stub.Infer(request, timeout=self.timeout_s)
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                self.timeouts += 1
                raise ModelTimeout(
                    f"{self.name} exceeded {self.timeout_s * 1000:.0f} ms"
                ) from exc
            raise

        if response.WhichOneof("action") == "trajectory":
            return TrajectoryAction(waypoints=[
                TrajectoryPoint(
                    x=p.x, y=p.y,
                    target_speed_mps=p.target_speed_mps or None,
                    timestamp_s=p.timestamp_s or None,
                )
                for p in response.trajectory.waypoints
            ])

        control = response.control
        return VehicleControlAction(
            throttle=control.throttle,
            steer=control.steer,
            brake=control.brake,
            hand_brake=control.hand_brake,
        )

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None

    # -- serialisation ----------------------------------------------------
    def _to_proto(self, obs: Observation) -> pb.Observation:
        msg = pb.Observation(
            frame_id=obs.frame_id,
            timestamp=obs.timestamp,
            speed_mps=obs.speed_mps,
            acceleration_mps2=obs.acceleration_mps2,
            steering_angle=obs.steering_angle,
            route_command=obs.route_command or "",
        )
        msg.ego_pose.x = obs.ego_pose.x
        msg.ego_pose.y = obs.ego_pose.y
        msg.ego_pose.z = obs.ego_pose.z
        msg.ego_pose.roll = obs.ego_pose.roll
        msg.ego_pose.pitch = obs.ego_pose.pitch
        msg.ego_pose.yaw = obs.ego_pose.yaw

        # Only ship sensors the model said it needs.
        if "rgb_front" in self.required_sensors and obs.rgb_front is not None:
            msg.rgb_front.CopyFrom(encode_image(obs.rgb_front, self.image_encoding))
        if "route" in self.required_sensors and obs.route_waypoints:
            for x, y in obs.route_waypoints:
                msg.route_waypoints.add(x=x, y=y)
        if "lead_vehicle" in self.required_sensors and obs.lead_vehicle is not None:
            msg.lead_vehicle.gap_m = obs.lead_vehicle.gap_m
            msg.lead_vehicle.speed_mps = obs.lead_vehicle.speed_mps
        return msg

    def health_check(self) -> bool:
        try:
            return self._stub.HealthCheck(
                pb.HealthCheckRequest(), timeout=self.timeout_s
            ).healthy
        except grpc.RpcError:
            return False
