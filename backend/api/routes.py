"""REST API (spec section 63)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.database.models import Episode, Event, Experiment, Metric, Model, Scenario
from backend.database.session import get_session
from backend.experiment_manager import IllegalTransition
from backend.schemas import (
    EpisodeOut,
    EventOut,
    ExperimentIn,
    ExperimentOut,
    MetricsOut,
    ModelHealth,
    ModelIn,
    ModelOut,
    ScenarioDetail,
    ScenarioOut,
)

router = APIRouter(prefix="/api")


def manager(request: Request):
    return request.app.state.manager


# --- models --------------------------------------------------------------
@router.get("/models", response_model=list[ModelOut], tags=["models"])
def list_models(db: Session = Depends(get_session)):
    return db.query(Model).order_by(Model.id).all()


@router.post("/models", response_model=ModelOut, status_code=201, tags=["models"])
def register_model(payload: ModelIn, db: Session = Depends(get_session)):
    if db.get(Model, payload.id) is not None:
        raise HTTPException(409, f"model {payload.id!r} already registered")
    model = Model(**payload.model_dump())
    db.add(model)
    db.commit()
    return model


@router.get("/models/{model_id}/health", response_model=ModelHealth, tags=["models"])
def model_health(model_id: str, db: Session = Depends(get_session)):
    model = db.get(Model, model_id)
    if model is None:
        raise HTTPException(404, f"unknown model {model_id!r}")

    # Imported lazily: the API is useful even where gRPC deps are unavailable.
    from model_gateway.adapters.remote import ModelUnavailable, RemoteModelAdapter

    try:
        adapter = RemoteModelAdapter(model.endpoint, connect_timeout_s=3.0)
    except ModelUnavailable as exc:
        return ModelHealth(id=model_id, endpoint=model.endpoint,
                           healthy=False, detail=str(exc))
    try:
        return ModelHealth(id=model_id, endpoint=model.endpoint,
                           healthy=adapter.health_check(), detail="")
    finally:
        adapter.close()


# --- scenarios -----------------------------------------------------------
@router.get("/scenarios", response_model=list[ScenarioOut], tags=["scenarios"])
def list_scenarios(db: Session = Depends(get_session)):
    return db.query(Scenario).order_by(Scenario.id).all()


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDetail, tags=["scenarios"])
def get_scenario(scenario_id: str, db: Session = Depends(get_session)):
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, f"unknown scenario {scenario_id!r}")
    return scenario


# --- experiments ---------------------------------------------------------
@router.post("/experiments", response_model=ExperimentOut, status_code=201,
             tags=["experiments"])
def create_experiment(payload: ExperimentIn, request: Request,
                      db: Session = Depends(get_session)):
    try:
        return manager(request).create(
            db, payload.model_id, payload.scenario_id, payload.seed
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/experiments", response_model=list[ExperimentOut], tags=["experiments"])
def list_experiments(db: Session = Depends(get_session), limit: int = 50):
    return (db.query(Experiment)
              .order_by(Experiment.created_at.desc())
              .limit(limit).all())


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut,
            tags=["experiments"])
def get_experiment(experiment_id: str, db: Session = Depends(get_session)):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")
    return experiment


@router.post("/experiments/{experiment_id}/start", response_model=ExperimentOut,
             tags=["experiments"])
def start_experiment(experiment_id: str, request: Request,
                     db: Session = Depends(get_session)):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")
    try:
        manager(request).start(db, experiment)
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    return experiment


@router.post("/experiments/{experiment_id}/stop", response_model=ExperimentOut,
             tags=["experiments"])
def stop_experiment(experiment_id: str, request: Request,
                    db: Session = Depends(get_session)):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")
    try:
        manager(request).stop(db, experiment)
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(experiment)
    return experiment


# --- results -------------------------------------------------------------
@router.get("/experiments/{experiment_id}/episodes", response_model=list[EpisodeOut],
            tags=["results"])
def experiment_episodes(experiment_id: str, db: Session = Depends(get_session)):
    if db.get(Experiment, experiment_id) is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")
    return db.query(Episode).filter(Episode.experiment_id == experiment_id).all()


@router.get("/experiments/{experiment_id}/metrics", response_model=MetricsOut,
            tags=["results"])
def experiment_metrics(experiment_id: str, db: Session = Depends(get_session)):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")
    rows = db.query(Metric).filter(Metric.experiment_id == experiment_id).all()
    return MetricsOut(
        experiment_id=experiment_id,
        status=experiment.status,
        metrics={row.name: row.value for row in rows},
    )


@router.get("/experiments/{experiment_id}/events", response_model=list[EventOut],
            tags=["results"])
def experiment_events(experiment_id: str, db: Session = Depends(get_session)):
    if db.get(Experiment, experiment_id) is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")
    return (db.query(Event)
              .filter(Event.experiment_id == experiment_id)
              .order_by(Event.time_s).all())


@router.get("/experiments/{experiment_id}/telemetry", tags=["results"])
def experiment_telemetry(experiment_id: str, db: Session = Depends(get_session),
                         limit: int = 5000):
    """Per-tick telemetry, read from the artefacts on disk rather than the DB."""
    episodes = (db.query(Episode)
                  .filter(Episode.experiment_id == experiment_id).all())
    if not episodes:
        raise HTTPException(404, f"no episode recorded for {experiment_id!r}")
    path = Path(episodes[0].artifacts_path) / "telemetry.jsonl"
    if not path.exists():
        raise HTTPException(404, "telemetry file is missing")
    with path.open() as fh:
        return [json.loads(line) for _, line in zip(range(limit), fh)]
