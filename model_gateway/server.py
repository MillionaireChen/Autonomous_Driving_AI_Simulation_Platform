"""Serve any DrivingPolicy over gRPC.

A model author writes a DrivingPolicy and calls serve(). Nothing in this module
knows what the policy is: a constant, a PID controller, a CNN, or a call out to
something else again.

This is the process boundary that makes the platform pluggable. The simulator
talks to this service and cannot reach into the model; the model receives
observations and cannot reach into CARLA.
"""

from __future__ import annotations

import io
import logging
from concurrent import futures
from typing import Optional

import grpc
import numpy as np

from model_gateway.protocol import driving_pb2 as pb
from model_gateway.protocol import driving_pb2_grpc as pb_grpc
from simulator.policy import DrivingPolicy
from simulator.types import (
    LeadVehicle, Observation, Pose, TrajectoryAction,
)

log = logging.getLogger(__name__)


def decode_image(image: pb.Image) -> Optional[np.ndarray]:
    """Proto image -> (H, W, 3) RGB array."""
    if not image.data:
        return None
    if image.encoding == "rgb8":
        return np.frombuffer(image.data, dtype=np.uint8).reshape(
            (image.height, image.width, 3)
        )
    if image.encoding == "jpeg":
        from PIL import Image as PILImage

        return np.asarray(PILImage.open(io.BytesIO(image.data)).convert("RGB"))
    raise ValueError(f"unsupported image encoding: {image.encoding!r}")


def observation_from_proto(msg: pb.Observation) -> Observation:
    return Observation(
        frame_id=msg.frame_id,
        timestamp=msg.timestamp,
        speed_mps=msg.speed_mps,
        acceleration_mps2=msg.acceleration_mps2,
        steering_angle=msg.steering_angle,
        ego_pose=Pose(
            x=msg.ego_pose.x, y=msg.ego_pose.y, z=msg.ego_pose.z,
            roll=msg.ego_pose.roll, pitch=msg.ego_pose.pitch, yaw=msg.ego_pose.yaw,
        ),
        rgb_front=decode_image(msg.rgb_front) if msg.HasField("rgb_front") else None,
        route_command=msg.route_command or None,
        route_waypoints=[(w.x, w.y) for w in msg.route_waypoints],
        lead_vehicle=(
            LeadVehicle(gap_m=msg.lead_vehicle.gap_m,
                        speed_mps=msg.lead_vehicle.speed_mps)
            if msg.HasField("lead_vehicle") else None
        ),
    )


class DrivingModelServicer(pb_grpc.DrivingModelServicer):
    def __init__(
        self,
        policy: DrivingPolicy,
        model_id: str,
        model_name: str = "",
        version: str = "0.1.0",
    ) -> None:
        self.policy = policy
        self.model_id = model_id
        self.model_name = model_name or model_id
        self.version = version

    def HealthCheck(self, request, context):
        return pb.HealthCheckResponse(healthy=True, detail="ok")

    def GetModelInfo(self, request, context):
        return pb.ModelInfo(
            id=self.model_id,
            name=self.model_name,
            type=(pb.ModelType.TRAJECTORY_POLICY
                  if getattr(self.policy, "model_type", "") == "TRAJECTORY_POLICY"
                  else pb.ModelType.CONTROL_POLICY),
            required_sensors=list(self.policy.required_sensors),
            version=self.version,
        )

    def ResetEpisode(self, request, context):
        try:
            self.policy.reset({
                "episode_id": request.episode_id,
                "fixed_delta_seconds": request.fixed_delta_seconds,
                "target_speed_mps": request.target_speed_mps,
                "spawn_index": request.spawn_index,
            })
            return pb.ResetEpisodeResponse(ok=True, detail="")
        except Exception as exc:  # surface it to the caller rather than dying
            log.exception("reset failed")
            return pb.ResetEpisodeResponse(ok=False, detail=str(exc))

    def Infer(self, request, context):
        try:
            action = self.policy.infer(observation_from_proto(request.observation))
        except Exception as exc:
            log.exception("inference failed")
            context.abort(grpc.StatusCode.INTERNAL, f"inference failed: {exc}")
            raise AssertionError("unreachable")  # abort() always raises

        if isinstance(action, TrajectoryAction):
            response = pb.InferResponse()
            for point in action.waypoints:
                response.trajectory.waypoints.add(
                    x=point.x, y=point.y,
                    target_speed_mps=point.target_speed_mps or 0.0,
                    timestamp_s=point.timestamp_s or 0.0,
                )
            return response

        return pb.InferResponse(control=pb.VehicleControl(
            throttle=action.throttle,
            steer=action.steer,
            brake=action.brake,
            hand_brake=action.hand_brake,
        ))


def serve(
    policy: DrivingPolicy,
    port: int,
    model_id: str,
    model_name: str = "",
    version: str = "0.1.0",
    max_workers: int = 4,
) -> None:
    """Run the model service until interrupted."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_DrivingModelServicer_to_server(
        DrivingModelServicer(policy, model_id, model_name, version), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"{model_id} serving on port {port} "
          f"(sensors: {list(policy.required_sensors) or 'none'})", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=2.0)
    finally:
        policy.close()
