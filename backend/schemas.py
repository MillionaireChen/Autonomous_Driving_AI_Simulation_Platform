"""Request and response shapes for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    name: str
    type: str
    endpoint: str
    timeout_ms: int
    gpu: Optional[int] = None


class ModelIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    name: str
    endpoint: str
    type: str = "CONTROL_POLICY"
    timeout_ms: int = 500
    gpu: Optional[int] = None


class ModelHealth(BaseModel):
    id: str
    endpoint: str
    healthy: bool
    detail: str = ""


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    map: str
    version: str
    duration_seconds: float
    default_seed: int


class ScenarioDetail(ScenarioOut):
    definition: dict[str, Any] = Field(default_factory=dict)


class ExperimentIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    scenario_id: str
    seed: int = 42
    record_frames: bool = False


class ArenaIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_a: str
    model_b: str
    scenario_id: str
    seed: int = 42
    record_frames: bool = False


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    model_id: str
    scenario_id: str
    seed: int
    record_frames: bool = False
    status: str
    score: Optional[float] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    versions: dict[str, Any] = Field(default_factory=dict)


class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: str
    collision: bool
    collision_count: int
    minimum_ttc: Optional[float]
    route_completion: float
    average_speed: float
    max_speed: float
    hard_brake_count: int
    lane_invasion_count: int
    model_latency_p50: float
    model_latency_p95: float
    model_timeouts: int
    ticks: int
    duration_s: float
    distance_m: float
    termination_reason: str
    result: str
    score: float
    artifacts_path: str


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time_s: float
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class MetricsOut(BaseModel):
    experiment_id: str
    status: str
    metrics: dict[str, Optional[float]] = Field(default_factory=dict)
