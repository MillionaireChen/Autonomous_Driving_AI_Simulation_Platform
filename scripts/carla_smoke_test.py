#!/usr/bin/env python3
"""Phase 1 acceptance test: prove the CARLA foundation actually works.

Runs the full lifecycle end to end against a real CARLA server:

    connect -> load Town04 -> spawn ego -> attach RGB camera
            -> capture frames -> throttle -> brake -> destroy everything

Every check is a hard numeric threshold, not a human judgement (spec 84.11).
The process exits 0 only if all of them pass.

Usage:
    uv run python scripts/carla_smoke_test.py
    uv run python scripts/carla_smoke_test.py --port 2000 --frames 20
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

import carla

REPO_ROOT = Path(__file__).resolve().parent.parent
SIM_CONFIG = REPO_ROOT / "configs" / "simulator" / "carla.yaml"
CAMERA_CONFIG = REPO_ROOT / "configs" / "sensors" / "front_camera.yaml"

# --- acceptance thresholds ------------------------------------------------
MIN_DISPLACEMENT_M = 5.0     # the car must genuinely move under throttle
MAX_STOPPED_SPEED_MPS = 0.5  # and must genuinely come to rest under braking
MIN_FRAME_STD = 1.0          # a frame this flat is a black/blank render


def log(step: str, detail: str = "") -> None:
    # Width fits the longest step label ("CARLA connection successful").
    print(f"{step:<29}{detail}", flush=True)


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def speed_of(actor: carla.Actor) -> float:
    v = actor.get_velocity()
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def to_rgb_array(image: carla.Image) -> np.ndarray:
    """CARLA delivers BGRA; return an (H, W, 3) RGB array."""
    buf = np.frombuffer(image.raw_data, dtype=np.uint8)
    buf = buf.reshape((image.height, image.width, 4))
    return buf[:, :, :3][:, :, ::-1]


@dataclass
class Result:
    passed: bool = False
    carla_client_version: str = ""
    carla_server_version: str = ""
    map_name: str = ""
    vehicle_id: int = -1
    frames_saved: int = 0
    frames_received: int = 0
    displacement_m: float = 0.0
    max_speed_mps: float = 0.0
    final_speed_mps: float = 0.0
    mean_frame_std: float = 0.0
    checks: dict = field(default_factory=dict)


def run(args: argparse.Namespace) -> Result:
    sim_cfg = load_yaml(SIM_CONFIG)
    cam_cfg = load_yaml(CAMERA_CONFIG)

    host = args.host or sim_cfg["server"]["host"]
    port = args.port or sim_cfg["server"]["port"]
    timeout = float(sim_cfg["server"]["timeout_seconds"])
    town = args.map or sim_cfg["world"]["map"]
    delta = float(sim_cfg["world"]["fixed_delta_seconds"])

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = Result()
    world = None
    original_settings = None
    camera = None
    vehicle = None

    try:
        # --- connect ----------------------------------------------------
        client = carla.Client(host, port)
        client.set_timeout(timeout)
        result.carla_client_version = client.get_client_version()
        result.carla_server_version = client.get_server_version()
        log("CARLA connection successful",
            f"client {result.carla_client_version} / server {result.carla_server_version}")

        # --- load the map ------------------------------------------------
        world = client.load_world(town)
        result.map_name = world.get_map().name
        log("Map loaded", result.map_name)

        # Deterministic weather so repeated runs render the same scene.
        world.set_weather(carla.WeatherParameters(
            cloudiness=10.0, precipitation=0.0, sun_altitude_angle=60.0))

        # --- synchronous fixed-timestep mode (spec 17/18) -----------------
        original_settings = world.get_settings()
        settings = world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = delta
        world.apply_settings(settings)
        log("Sync mode enabled", f"fixed_delta_seconds={delta} ({int(1 / delta)} steps/s)")

        # --- spawn the ego vehicle (spec 19) ------------------------------
        blueprints = world.get_blueprint_library()
        ego_bp = blueprints.find("vehicle.tesla.model3")
        ego_bp.set_attribute("role_name", "ego")

        spawn_points = world.get_map().get_spawn_points()
        if not spawn_points:
            raise RuntimeError(f"{town} exposes no spawn points")

        # Deterministic choice, walking forward only if a point is occupied.
        vehicle = None
        for offset in range(len(spawn_points)):
            idx = (args.spawn_index + offset) % len(spawn_points)
            vehicle = world.try_spawn_actor(ego_bp, spawn_points[idx])
            if vehicle is not None:
                break
        if vehicle is None:
            raise RuntimeError("could not spawn the ego vehicle at any spawn point")
        result.vehicle_id = vehicle.id
        log("Vehicle spawned", f"{ego_bp.id} id={vehicle.id} spawn_point={idx}")

        # --- attach the front camera, configured from YAML (spec 21) ------
        cam_bp = blueprints.find("sensor.camera.rgb")
        cam_bp.set_attribute("image_size_x", str(cam_cfg["width"]))
        cam_bp.set_attribute("image_size_y", str(cam_cfg["height"]))
        cam_bp.set_attribute("fov", str(cam_cfg["fov"]))
        cam_bp.set_attribute("sensor_tick", str(cam_cfg["sensor_tick"]))
        tf = cam_cfg["transform"]
        cam_tf = carla.Transform(
            carla.Location(x=float(tf["x"]), y=float(tf["y"]), z=float(tf["z"])),
            carla.Rotation(pitch=float(tf["pitch"])),
        )
        camera = world.spawn_actor(cam_bp, cam_tf, attach_to=vehicle)

        images: queue.Queue = queue.Queue()
        camera.listen(images.put)
        log("Camera active",
            f"{cam_cfg['width']}x{cam_cfg['height']} fov={cam_cfg['fov']} "
            f"@{1 / float(cam_cfg['sensor_tick']):.0f}Hz")

        frame_stds: list[float] = []

        def drain(save_budget: int) -> int:
            """Pull whatever the camera produced this tick; save up to budget."""
            saved = 0
            while True:
                try:
                    image = images.get_nowait()
                except queue.Empty:
                    return saved
                result.frames_received += 1
                if saved < save_budget and result.frames_saved < args.frames:
                    rgb = to_rgb_array(image)
                    frame_stds.append(float(rgb.std()))
                    result.frames_saved += 1
                    Image.fromarray(rgb).save(
                        out_dir / f"{result.frames_saved:06d}.jpg", quality=90)
                    saved += 1

        # Let the suspension settle before measuring anything.
        vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
        for _ in range(20):
            world.tick()
            drain(0)

        # --- drive under throttle -----------------------------------------
        start_loc = vehicle.get_location()
        vehicle.apply_control(carla.VehicleControl(throttle=0.4, steer=0.0, brake=0.0))
        drive_ticks = int(args.drive_seconds / delta)
        for _ in range(drive_ticks):
            world.tick()
            drain(args.frames)
            result.max_speed_mps = max(result.max_speed_mps, speed_of(vehicle))

        end_loc = vehicle.get_location()
        result.displacement_m = start_loc.distance(end_loc)
        log("Vehicle moved",
            f"displacement {result.displacement_m:.1f} m, "
            f"peak speed {result.max_speed_mps:.1f} m/s")

        # --- brake to a standstill -----------------------------------------
        vehicle.apply_control(carla.VehicleControl(throttle=0.0, steer=0.0, brake=1.0))
        brake_ticks = int(args.brake_seconds / delta)
        for _ in range(brake_ticks):
            world.tick()
            drain(args.frames)
            if speed_of(vehicle) < MAX_STOPPED_SPEED_MPS:
                break
        result.final_speed_mps = speed_of(vehicle)
        log("Vehicle stopped", f"final speed {result.final_speed_mps:.2f} m/s")

        # Give the last in-flight images a moment to land.
        deadline = time.time() + 2.0
        while result.frames_saved < args.frames and time.time() < deadline:
            world.tick()
            drain(args.frames)

        result.mean_frame_std = float(np.mean(frame_stds)) if frame_stds else 0.0
        log(f"{result.frames_saved} frames received",
            f"saved to {out_dir}/ (received {result.frames_received} total)")

        # --- deterministic verdict ------------------------------------------
        result.checks = {
            "frames_saved": result.frames_saved >= args.frames,
            "frames_not_blank": result.mean_frame_std >= MIN_FRAME_STD,
            "vehicle_moved": result.displacement_m > MIN_DISPLACEMENT_M,
            "vehicle_stopped": result.final_speed_mps < MAX_STOPPED_SPEED_MPS,
        }
        result.passed = all(result.checks.values())
        return result

    finally:
        # Tear down in dependency order and always hand the server back in
        # asynchronous mode; leaving it synchronous wedges the next client.
        if camera is not None:
            camera.stop()
            camera.destroy()
        if vehicle is not None:
            vehicle.destroy()
        if world is not None and original_settings is not None:
            world.apply_settings(original_settings)
        log("Cleanup successful", "actors destroyed, server back in async mode")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--map", default=None)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--drive-seconds", type=float, default=6.0)
    parser.add_argument("--brake-seconds", type=float, default=5.0)
    parser.add_argument("--spawn-index", type=int, default=0)
    parser.add_argument("--output", default=str(REPO_ROOT / "output" / "smoke_test"))
    args = parser.parse_args()

    started = time.time()
    result = run(args)
    elapsed = time.time() - started

    out_dir = Path(args.output)
    (out_dir / "result.json").write_text(json.dumps(asdict(result), indent=2))

    print()
    for name, ok in result.checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\nPhase 1 smoke test: {'PASS' if result.passed else 'FAIL'} ({elapsed:.1f}s)")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
