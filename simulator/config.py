"""Configuration loading.

Every tunable lives in YAML under configs/, never hard-coded in Python
(spec section 84.9). Environment variables from .env override the connection
settings so the same config works on another machine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "configs"


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_absolute():
        path = CONFIG_DIR / path
    with path.open() as fh:
        return yaml.safe_load(fh)


def load_simulator_config() -> dict[str, Any]:
    """Simulator config with .env overrides applied."""
    cfg = load_yaml("simulator/carla.yaml")
    server = cfg.setdefault("server", {})
    if host := os.environ.get("CARLA_HOST"):
        server["host"] = host
    if port := os.environ.get("CARLA_RPC_PORT"):
        server["port"] = int(port)
    if gpu := os.environ.get("CARLA_GPU"):
        cfg.setdefault("gpu", {})["device"] = int(gpu)
    return cfg


def load_camera_config() -> dict[str, Any]:
    return load_yaml("sensors/front_camera.yaml")


def available_cameras() -> dict[str, dict[str, Any]]:
    """Every named camera any rig file in configs/sensors/ defines."""
    cameras: dict[str, dict[str, Any]] = {}
    for path in sorted((CONFIG_DIR / "sensors").glob("*.yaml")):
        rig = load_yaml(path) or {}
        for name, spec in (rig.get("cameras") or {}).items():
            if name in cameras and cameras[name] != spec:
                raise ValueError(
                    f"camera {name!r} is defined twice with different specs; "
                    f"the second is in {path.name}"
                )
            cameras[name] = spec
    return cameras


def resolve_camera_rig(required_sensors) -> dict[str, dict[str, Any]]:
    """The extra cameras a model asked for, ready to mount.

    The model names the views it wants in `required_sensors`; this finds them
    among the rig files. Driving it from the model's own declaration rather
    than a per-model config means registering a model is one entry in
    models.yaml, not two places that can disagree - and a model that asks for
    a camera nobody defines fails loudly here instead of quietly receiving a
    black frame.
    """
    catalogue = available_cameras()
    wanted = [s for s in required_sensors if s in catalogue]
    unknown = [
        s for s in required_sensors
        if s.startswith("cam_") and s not in catalogue
    ]
    if unknown:
        raise ValueError(
            f"model requires camera(s) {unknown} that no rig in "
            f"configs/sensors/ defines"
        )
    return {name: catalogue[name] for name in wanted}


def load_episode_config() -> dict[str, Any]:
    return load_yaml("simulator/episode.yaml")
