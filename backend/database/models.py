"""Database schema (spec section 38).

Images and frames never go in here. The database holds metadata and results;
the bulky artefacts stay on disk under output/ (spec section 42).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Model(Base):
    """A registered driving model (spec section 16)."""

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(32), default="CONTROL_POLICY")
    endpoint: Mapped[str] = mapped_column(String(128))
    timeout_ms: Mapped[int] = mapped_column(Integer, default=500)
    gpu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Order in the dashboard list; the first entry is selected by default.
    display_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="model")


class Scenario(Base):
    """A scenario definition, mirrored from its YAML file."""

    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    map: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    duration_seconds: Mapped[float] = mapped_column(Float, default=40.0)
    default_seed: Mapped[int] = mapped_column(Integer, default=42)
    # YAML stem this was loaded from; the scenario id and the filename differ
    # (highway_cut_in.yaml defines highway_cut_in_001).
    source: Mapped[str] = mapped_column(String(128), default="")
    # The full YAML, so a result can be reproduced even if the file changes.
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="scenario")


class Experiment(Base):
    """model + scenario + seed + simulator config (spec sections 37, 39)."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id"))
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"))
    seed: Mapped[int] = mapped_column(Integer, default=42)
    record_frames: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(16), default="CREATED", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Everything needed to reproduce this run (spec section 75).
    versions: Mapped[dict] = mapped_column(JSON, default=dict)

    model: Mapped[Model] = relationship(back_populates="experiments")
    scenario: Mapped[Scenario] = relationship(back_populates="experiments")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    metrics: Mapped[list["Metric"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    frames: Mapped[list["Frame"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class Episode(Base):
    """The scored outcome of one run (spec section 40)."""

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)

    collision: Mapped[bool] = mapped_column(Boolean, default=False)
    collision_count: Mapped[int] = mapped_column(Integer, default=0)
    minimum_ttc: Mapped[float | None] = mapped_column(Float, nullable=True)
    route_completion: Mapped[float] = mapped_column(Float, default=0.0)
    average_speed: Mapped[float] = mapped_column(Float, default=0.0)
    max_speed: Mapped[float] = mapped_column(Float, default=0.0)
    hard_brake_count: Mapped[int] = mapped_column(Integer, default=0)
    lane_invasion_count: Mapped[int] = mapped_column(Integer, default=0)

    model_latency_p50: Mapped[float] = mapped_column(Float, default=0.0)
    model_latency_p95: Mapped[float] = mapped_column(Float, default=0.0)
    model_timeouts: Mapped[int] = mapped_column(Integer, default=0)

    ticks: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    termination_reason: Mapped[str] = mapped_column(String(32), default="")

    result: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    score: Mapped[float] = mapped_column(Float, default=0.0)

    # Where the frames, telemetry and metrics.json live (spec section 42).
    artifacts_path: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    experiment: Mapped[Experiment] = relationship(back_populates="episodes")


class Frame(Base):
    """Metadata for one recorded camera frame (spec sections 38, 42).

    The image itself is a JPEG on disk; only its location is stored here.
    """

    __tablename__ = "frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    tick: Mapped[int] = mapped_column(Integer)
    sim_time: Mapped[float] = mapped_column(Float)
    path: Mapped[str] = mapped_column(String(256))

    experiment: Mapped[Experiment] = relationship(back_populates="frames")


class Event(Base):
    """Notable moments during an episode (spec section 41)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    time_s: Mapped[float] = mapped_column(Float, default=0.0)
    type: Mapped[str] = mapped_column(String(48), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

    experiment: Mapped[Experiment] = relationship(back_populates="events")


class Metric(Base):
    """Flat key/value metrics, so results can be queried and compared."""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)

    experiment: Mapped[Experiment] = relationship(back_populates="metrics")
