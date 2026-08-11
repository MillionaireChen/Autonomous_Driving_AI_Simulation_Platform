"""The closed-loop simulation worker.

One episode is:

    observation -> policy -> action -> world tick -> next observation

The worker owns the loop, the clock and the safety envelope. The policy only
ever sees an Observation and returns an action; it cannot touch CARLA
(spec section 73).

The simulation runs at a fixed 20 Hz while the policy is asked at a lower rate
(10 Hz by default, spec section 17). On ticks in between, the previous action
is reapplied, which is what a real vehicle does between controller updates.

A scenario, when supplied, drives everything else in the world: it spawns the
vehicles, decides when to act and can end the episode early. The worker does
not know what any particular scenario means.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import carla

from simulator import carla_client, ego as ego_mod
from simulator.policy import DrivingPolicy
from simulator.scenario import ScenarioConfig, ScenarioContext, ScenarioRunner
from simulator.sensors import CameraSensor, CollisionSensor
from simulator.types import (
    EpisodeResult,
    Observation,
    VehicleControlAction,
    percentile,
    safety_fallback,
)


class SimulationWorker:
    """Runs closed-loop episodes against a CARLA server."""

    def __init__(
        self,
        sim_config: dict[str, Any],
        camera_config: dict[str, Any],
        ego_config: dict[str, Any],
        episode_config: dict[str, Any],
    ) -> None:
        self.sim_config = sim_config
        self.camera_config = camera_config
        self.ego_config = ego_config
        self.episode_config = episode_config

        self.fixed_delta = float(sim_config["world"]["fixed_delta_seconds"])
        self.sim_hz = 1.0 / self.fixed_delta

        inference_hz = float(episode_config.get("inference_hz", 10))
        self.inference_interval = max(1, round(self.sim_hz / inference_hz))

    # -- observation ------------------------------------------------------
    def _observe(
        self,
        vehicle: carla.Vehicle,
        camera: Optional[CameraSensor],
        frame_id: int,
        sim_time: float,
        route_command: Optional[str],
    ) -> Observation:
        control = vehicle.get_control()
        return Observation(
            frame_id=frame_id,
            timestamp=sim_time,
            speed_mps=ego_mod.speed_of(vehicle),
            acceleration_mps2=ego_mod.longitudinal_acceleration(vehicle),
            steering_angle=control.steer,
            ego_pose=ego_mod.pose_of(vehicle),
            rgb_front=camera.poll() if camera else None,
            route_command=route_command,
        )

    # -- episode ----------------------------------------------------------
    def run_episode(
        self,
        policy: DrivingPolicy,
        episode_id: str = "EP-0001",
        output_dir: Optional[Path] = None,
        scenario: Optional[ScenarioConfig] = None,
    ) -> EpisodeResult:
        server = self.sim_config["server"]
        world_cfg = self.sim_config["world"]

        # A scenario, where present, is the authority on the world it needs.
        town = scenario.map if scenario else world_cfg["map"]
        weather = scenario.weather if scenario else self.episode_config.get("weather")
        duration = float(
            scenario.duration_seconds if scenario
            else self.episode_config["duration_seconds"]
        )
        spawn_index = int(
            (scenario.ego.get("spawn_index") if scenario else None)
            or self.episode_config.get("spawn_index", 0)
        )
        target_speed = (
            (scenario.ego.get("target_speed_mps") if scenario else None)
            or self.ego_config.get("target_speed_mps")
        )
        max_ticks = int(duration / self.fixed_delta)

        result = EpisodeResult(
            episode_id=episode_id,
            policy_name=policy.name,
            scenario_id=scenario.id if scenario else "",
            status="RUNNING",
        )
        latencies: list[float] = []
        telemetry: list[dict[str, Any]] = []
        wall_start = time.time()

        client = carla_client.connect(
            server["host"], server["port"], float(server["timeout_seconds"])
        )
        result.versions = {
            "carla_client": client.get_client_version(),
            "carla_server": client.get_server_version(),
            "scenario_version": scenario.version if scenario else "",
        }

        vehicle = None
        camera = None
        collision = None
        runner = ScenarioRunner(scenario) if scenario else None

        with carla_client.synchronous_world(
            client, town, self.fixed_delta, weather=weather
        ) as world:
            try:
                result.map_name = world.get_map().name

                vehicle, used_spawn = ego_mod.spawn_ego(
                    world, self.ego_config, spawn_index=spawn_index
                )
                camera = CameraSensor(world, vehicle, self.camera_config)
                collision = CollisionSensor(world, vehicle)

                # Let the suspension settle before the episode clock starts,
                # otherwise the first metres are the car dropping onto the road.
                #
                # This must also happen before the scenario is set up: a freshly
                # spawned actor reports a stale location to the client until the
                # world has ticked, and placing the NPC relative to a stale ego
                # pose puts it somewhere else entirely.
                vehicle.apply_control(carla.VehicleControl(brake=1.0))
                for _ in range(int(self.episode_config.get("settle_ticks", 20))):
                    world.tick()
                    if camera:
                        camera.poll()

                if runner is not None:
                    runner.setup(world, vehicle)
                    runner.log_event(0.0, "EPISODE_STARTED", {
                        "scenario": scenario.id, "seed": scenario.seed,
                    })
                    # Give the scenario actors a tick to become real too.
                    world.tick()

                policy.reset({
                    "episode_id": episode_id,
                    "fixed_delta_seconds": self.fixed_delta,
                    "target_speed_mps": target_speed,
                    "spawn_index": used_spawn,
                })

                action = VehicleControlAction()
                previous_pose = ego_mod.pose_of(vehicle)
                speed_sum = 0.0
                route_command = self.episode_config.get("route_command")
                terminate_on_collision = bool(
                    scenario.termination.get("collision", True)
                ) if scenario else False

                for tick in range(max_ticks):
                    sim_time = tick * self.fixed_delta
                    obs = self._observe(vehicle, camera, tick, sim_time, route_command)

                    # Ask the policy only at its own rate; reuse in between.
                    if tick % self.inference_interval == 0:
                        started = time.perf_counter()
                        try:
                            proposed = policy.infer(obs)
                        except Exception:
                            proposed = safety_fallback(action.steer)
                            result.invalid_actions += 1
                        latencies.append((time.perf_counter() - started) * 1000.0)
                        result.inferences += 1

                        # The simulator, not the model, decides what is legal.
                        if proposed is None or not proposed.is_finite():
                            proposed = safety_fallback(action.steer)
                            result.invalid_actions += 1
                        action = proposed.clamped()

                    vehicle.apply_control(carla.VehicleControl(
                        throttle=action.throttle,
                        steer=action.steer,
                        brake=action.brake,
                        hand_brake=action.hand_brake,
                    ))

                    # The scenario moves its own actors in the same tick.
                    if runner is not None:
                        runner.tick(ScenarioContext(
                            world=world, map=world.get_map(), ego=vehicle,
                            npc=runner.npc, npc_controller=runner.npc_controller,
                            sim_time=sim_time, dt=self.fixed_delta,
                        ))

                    world.tick()

                    pose = ego_mod.pose_of(vehicle)
                    speed = ego_mod.speed_of(vehicle)
                    result.distance_m += pose.distance_to(previous_pose)
                    result.max_speed_mps = max(result.max_speed_mps, speed)
                    speed_sum += speed
                    previous_pose = pose
                    result.ticks += 1

                    row = {
                        "tick": tick,
                        "sim_time": round(sim_time, 3),
                        "speed_mps": round(speed, 4),
                        "throttle": round(action.throttle, 4),
                        "steer": round(action.steer, 4),
                        "brake": round(action.brake, 4),
                        "x": round(pose.x, 3),
                        "y": round(pose.y, 3),
                    }
                    if runner is not None:
                        row.update(runner.npc_state(vehicle))
                    telemetry.append(row)

                    # -- termination (spec section 24) ----------------------
                    if collision.count and terminate_on_collision:
                        result.termination_reason = "COLLISION"
                        if runner is not None:
                            runner.log_event(sim_time, "COLLISION", collision.events[-1])
                        break
                else:
                    result.termination_reason = "TIMEOUT" if scenario else "DURATION"

                result.collisions = collision.count
                result.average_speed_mps = speed_sum / max(1, result.ticks)
                result.camera_frames = camera.frame_count if camera else 0
                result.status = "COMPLETED"

                if runner is not None:
                    result.scenario_triggered = runner.triggered
                    result.scenario_triggered_at = runner.triggered_at
                    result.events = runner.events

            finally:
                policy.close()
                extra = runner.actors() if runner is not None else []
                carla_client.destroy_actors(
                    [camera.actor if camera else None,
                     collision.actor if collision else None,
                     vehicle, *extra]
                )

        result.simulated_seconds = result.ticks * self.fixed_delta
        result.wall_seconds = time.time() - wall_start
        result.inference_latency_ms_p50 = percentile(latencies, 50)
        result.inference_latency_ms_p95 = percentile(latencies, 95)
        # Remote adapters track deadline misses; in-process policies have none.
        result.model_timeouts = int(getattr(policy, "timeouts", 0))

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "episode.json").write_text(json.dumps(asdict(result), indent=2))
            with (output_dir / "telemetry.jsonl").open("w") as fh:
                for row in telemetry:
                    fh.write(json.dumps(row) + "\n")
            with (output_dir / "events.jsonl").open("w") as fh:
                for event in result.events:
                    fh.write(json.dumps(event) + "\n")

        return result
