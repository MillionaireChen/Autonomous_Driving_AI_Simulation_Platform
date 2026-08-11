"""Tests for the gRPC model gateway.

These run a real gRPC server and a real client against it on a loopback port.
No CARLA is involved, so this is safe for CI (spec section 72) while still
exercising the actual protocol rather than a mock of it.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from model_gateway.adapters.remote import (
    ModelTimeout,
    RemoteModelAdapter,
    encode_image,
)
from model_gateway.protocol import driving_pb2 as pb
from model_gateway.server import decode_image, observation_from_proto
from simulator.policy import DrivingPolicy
from simulator.types import Observation, Pose, VehicleControlAction


# --- helpers -------------------------------------------------------------
def make_observation(with_image: bool = False) -> Observation:
    image = None
    if with_image:
        rng = np.random.default_rng(0)
        image = rng.integers(0, 256, size=(12, 16, 3), dtype=np.uint8)
    return Observation(
        frame_id=7,
        timestamp=0.35,
        speed_mps=12.5,
        acceleration_mps2=-1.25,
        steering_angle=0.125,
        ego_pose=Pose(x=1.5, y=-2.5, z=0.25, roll=0.0, pitch=-5.0, yaw=90.0),
        rgb_front=image,
        route_command="LANE_FOLLOW",
    )


class RecordingPolicy(DrivingPolicy):
    """Echoes a fixed action and records what it was asked."""

    name = "recording"

    def __init__(self, required_sensors=(), delay_s: float = 0.0):
        self.required_sensors = tuple(required_sensors)
        self.delay_s = delay_s
        self.observations: list[Observation] = []
        self.resets: list[dict[str, Any]] = []

    def reset(self, config):
        self.resets.append(config)

    def infer(self, observation):
        self.observations.append(observation)
        if self.delay_s:
            time.sleep(self.delay_s)
        return VehicleControlAction(throttle=0.4, steer=-0.1, brake=0.0)


@pytest.fixture
def serving():
    """Start a gateway on an ephemeral port; yield (policy, adapter_factory)."""
    from concurrent import futures

    import grpc

    from model_gateway.protocol import driving_pb2_grpc as pb_grpc
    from model_gateway.server import DrivingModelServicer

    servers = []

    def start(policy: DrivingPolicy, **adapter_kwargs) -> RemoteModelAdapter:
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        pb_grpc.add_DrivingModelServicer_to_server(
            DrivingModelServicer(policy, "test-model", "Test Model", "9.9.9"), server
        )
        port = server.add_insecure_port("127.0.0.1:0")  # let the OS pick
        server.start()
        servers.append(server)
        return RemoteModelAdapter(f"127.0.0.1:{port}", **adapter_kwargs)

    yield start

    for server in servers:
        server.stop(grace=0)


# --- image codec ---------------------------------------------------------
class TestImageCodec:
    def test_rgb8_round_trip_is_lossless(self):
        rng = np.random.default_rng(1)
        original = rng.integers(0, 256, size=(9, 11, 3), dtype=np.uint8)
        decoded = decode_image(encode_image(original, "rgb8"))
        np.testing.assert_array_equal(decoded, original)

    def test_jpeg_round_trip_preserves_shape(self):
        rng = np.random.default_rng(2)
        original = rng.integers(0, 256, size=(16, 24, 3), dtype=np.uint8)
        decoded = decode_image(encode_image(original, "jpeg"))
        assert decoded.shape == original.shape

    def test_jpeg_is_smaller_than_raw_for_a_flat_image(self):
        flat = np.full((64, 64, 3), 128, dtype=np.uint8)
        assert len(encode_image(flat, "jpeg").data) < len(encode_image(flat, "rgb8").data)

    def test_unknown_encoding_is_rejected(self):
        with pytest.raises(ValueError):
            encode_image(np.zeros((2, 2, 3), np.uint8), "webp")

    def test_empty_image_decodes_to_none(self):
        assert decode_image(pb.Image()) is None


# --- observation conversion ---------------------------------------------
class TestObservationConversion:
    def test_scalar_fields_survive_the_round_trip(self):
        adapter = RemoteModelAdapter.__new__(RemoteModelAdapter)
        adapter.required_sensors = ()
        adapter.image_encoding = "rgb8"

        source = make_observation()
        restored = observation_from_proto(adapter._to_proto(source))

        assert restored.frame_id == source.frame_id
        assert restored.timestamp == pytest.approx(source.timestamp)
        assert restored.speed_mps == pytest.approx(source.speed_mps)
        assert restored.acceleration_mps2 == pytest.approx(source.acceleration_mps2)
        assert restored.steering_angle == pytest.approx(source.steering_angle)
        assert restored.route_command == "LANE_FOLLOW"

    def test_pose_survives_the_round_trip(self):
        adapter = RemoteModelAdapter.__new__(RemoteModelAdapter)
        adapter.required_sensors = ()
        adapter.image_encoding = "rgb8"

        restored = observation_from_proto(adapter._to_proto(make_observation()))
        assert restored.ego_pose == make_observation().ego_pose


# --- live service --------------------------------------------------------
class TestLiveService:
    def test_health_check(self, serving):
        adapter = serving(RecordingPolicy())
        assert adapter.health_check() is True

    def test_model_info_is_reported(self, serving):
        adapter = serving(RecordingPolicy(required_sensors=("rgb_front",)))
        assert adapter.model_id == "test-model"
        assert adapter.name == "Test Model"
        assert adapter.version == "9.9.9"
        assert adapter.model_type == "CONTROL_POLICY"
        assert adapter.required_sensors == ("rgb_front",)

    def test_reset_reaches_the_policy(self, serving):
        policy = RecordingPolicy()
        adapter = serving(policy)
        adapter.reset({"episode_id": "EP-42", "fixed_delta_seconds": 0.05,
                       "target_speed_mps": 15.0, "spawn_index": 3})
        assert policy.resets[-1]["episode_id"] == "EP-42"
        assert policy.resets[-1]["spawn_index"] == 3

    def test_infer_returns_the_policy_action(self, serving):
        adapter = serving(RecordingPolicy())
        action = adapter.infer(make_observation())
        assert action.throttle == pytest.approx(0.4)
        assert action.steer == pytest.approx(-0.1)

    def test_a_policy_that_raises_surfaces_as_an_error(self, serving):
        class Exploding(RecordingPolicy):
            def infer(self, observation):
                raise RuntimeError("boom")

        adapter = serving(Exploding())
        with pytest.raises(Exception):
            adapter.infer(make_observation())


class TestSensorGating:
    """Only sensors the model asked for are put on the wire (section 49)."""

    def test_image_is_sent_when_required(self, serving):
        policy = RecordingPolicy(required_sensors=("rgb_front",))
        adapter = serving(policy)
        adapter.infer(make_observation(with_image=True))
        assert policy.observations[-1].rgb_front is not None
        assert policy.observations[-1].rgb_front.shape == (12, 16, 3)

    def test_image_is_withheld_when_not_required(self, serving):
        policy = RecordingPolicy(required_sensors=())
        adapter = serving(policy)
        adapter.infer(make_observation(with_image=True))
        # The camera produced a frame, but this model never declared it.
        assert policy.observations[-1].rgb_front is None

    def test_withholding_shrinks_the_payload(self):
        adapter = RemoteModelAdapter.__new__(RemoteModelAdapter)
        adapter.image_encoding = "rgb8"
        obs = make_observation(with_image=True)

        adapter.required_sensors = ("rgb_front",)
        with_image = adapter._to_proto(obs).ByteSize()
        adapter.required_sensors = ()
        without_image = adapter._to_proto(obs).ByteSize()

        assert without_image < with_image


class TestDeadline:
    """A slow model is a failed inference, not a stalled simulation (section 50)."""

    def test_a_model_over_budget_raises_model_timeout(self, serving):
        adapter = serving(RecordingPolicy(delay_s=0.5), timeout_ms=50)
        with pytest.raises(ModelTimeout):
            adapter.infer(make_observation())

    def test_timeouts_are_counted(self, serving):
        adapter = serving(RecordingPolicy(delay_s=0.5), timeout_ms=50)
        for _ in range(3):
            with pytest.raises(ModelTimeout):
                adapter.infer(make_observation())
        assert adapter.timeouts == 3

    def test_a_model_inside_budget_does_not_time_out(self, serving):
        adapter = serving(RecordingPolicy(), timeout_ms=2000)
        adapter.infer(make_observation())
        assert adapter.timeouts == 0
