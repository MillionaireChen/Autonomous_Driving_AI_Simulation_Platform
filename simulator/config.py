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


def load_episode_config() -> dict[str, Any]:
    return load_yaml("simulator/episode.yaml")
