"""REST API (spec section 63)."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from simulator.types import percentile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.database.models import (
    Episode, Event, Experiment, Frame, Metric, Model, Scenario,
)
from backend.database.session import get_session
from backend.experiment_manager import IllegalTransition
from backend.schemas import (
    ArenaIn,
    BatchIn,
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
            db, payload.model_id, payload.scenario_id, payload.seed,
            record_frames=payload.record_frames,
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


# --- replay (spec section 58) --------------------------------------------
@router.get("/experiments/{experiment_id}/replay", tags=["replay"])
def experiment_replay(experiment_id: str, db: Session = Depends(get_session)):
    """Everything needed to replay a finished episode.

    Telemetry and events come off disk rather than the database: they are
    per-tick arrays, and the artefacts on disk are already the record
    (spec section 42). The database keeps the summary and the index.
    """
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, f"unknown experiment {experiment_id!r}")

    episodes = db.query(Episode).filter(Episode.experiment_id == experiment_id).all()
    if not episodes:
        raise HTTPException(404, f"no episode recorded for {experiment_id!r}")
    episode = episodes[0]
    artifacts = Path(episode.artifacts_path)

    telemetry: list[dict] = []
    telemetry_file = artifacts / "telemetry.jsonl"
    if telemetry_file.exists():
        with telemetry_file.open() as fh:
            telemetry = [json.loads(line) for line in fh]

    frames = (db.query(Frame)
                .filter(Frame.experiment_id == experiment_id)
                .order_by(Frame.index).all())

    return {
        "experiment_id": experiment_id,
        "scenario_id": experiment.scenario_id,
        "model_id": experiment.model_id,
        "seed": experiment.seed,
        "result": episode.result,
        "score": episode.score,
        "termination_reason": episode.termination_reason,
        "ticks": episode.ticks,
        "duration_s": episode.duration_s,
        "has_frames": len(frames) > 0,
        "frames": [
            {"index": f.index, "tick": f.tick, "sim_time": f.sim_time}
            for f in frames
        ],
        "telemetry": telemetry,
        "events": [
            {"time": e.time_s, "type": e.type, "data": e.data}
            for e in sorted(episode.experiment.events, key=lambda e: e.time_s)
        ],
    }


@router.get("/experiments/{experiment_id}/frames/{index}", tags=["replay"])
def experiment_frame(experiment_id: str, index: int,
                     db: Session = Depends(get_session)):
    """One recorded JPEG, served from disk."""
    frame = (db.query(Frame)
               .filter(Frame.experiment_id == experiment_id, Frame.index == index)
               .one_or_none())
    if frame is None:
        raise HTTPException(404, f"no frame {index} for {experiment_id!r}")

    episodes = db.query(Episode).filter(Episode.experiment_id == experiment_id).all()
    path = Path(episodes[0].artifacts_path) / frame.path
    # The stored path is relative and generated by us, but resolve and check
    # anyway: never let a path out of the artefacts directory reach the client.
    root = Path(episodes[0].artifacts_path).resolve()
    if not path.resolve().is_relative_to(root) or not path.exists():
        raise HTTPException(404, "frame file is missing")
    return FileResponse(path, media_type="image/jpeg")


# --- arena (spec section 60) ---------------------------------------------
@router.post("/arena", status_code=201, tags=["arena"])
def create_arena(payload: ArenaIn, request: Request,
                 db: Session = Depends(get_session)):
    """Run two models against an identical scenario, seed and simulator config.

    The comparison is only meaningful if nothing else differs, so both
    experiments are created from the same scenario and seed and run back to
    back on the one CARLA server.
    """
    if payload.model_a == payload.model_b:
        raise HTTPException(400, "pick two different models")
    try:
        experiments = [
            manager(request).create(db, model_id, payload.scenario_id, payload.seed,
                                    record_frames=payload.record_frames)
            for model_id in (payload.model_a, payload.model_b)
        ]
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    manager(request).start_many(db, experiments)
    return {
        "scenario_id": payload.scenario_id,
        "seed": payload.seed,
        "experiment_a": experiments[0].id,
        "experiment_b": experiments[1].id,
    }


@router.get("/compare", tags=["arena"])
def compare(a: str, b: str, db: Session = Depends(get_session)):
    """Side-by-side results for two experiments."""
    def side(experiment_id: str) -> dict:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise HTTPException(404, f"unknown experiment {experiment_id!r}")
        episodes = (db.query(Episode)
                      .filter(Episode.experiment_id == experiment_id).all())
        episode = episodes[0] if episodes else None
        return {
            "experiment_id": experiment.id,
            "model_id": experiment.model_id,
            "status": experiment.status,
            "scenario_id": experiment.scenario_id,
            "seed": experiment.seed,
            "result": episode.result if episode else None,
            "score": episode.score if episode else None,
            "collisions": episode.collision_count if episode else None,
            "minimum_ttc": episode.minimum_ttc if episode else None,
            "lane_invasions": episode.lane_invasion_count if episode else None,
            "route_completion": episode.route_completion if episode else None,
            "average_speed": episode.average_speed if episode else None,
            "distance_m": episode.distance_m if episode else None,
            "latency_p50": episode.model_latency_p50 if episode else None,
            "latency_p95": episode.model_latency_p95 if episode else None,
            "termination_reason": episode.termination_reason if episode else None,
        }

    left, right = side(a), side(b)
    fair = (left["scenario_id"] == right["scenario_id"]
            and left["seed"] == right["seed"])
    return {
        "fair": fair,
        "detail": "" if fair else "different scenario or seed; not comparable",
        "a": left,
        "b": right,
    }


@router.get("/simulators", tags=["meta"])
def simulators(request: Request):
    """The CARLA servers experiments can be placed on, and which are busy."""
    pool = manager(request).pool
    if pool is None:
        return {"simulators": [], "size": 0, "available": 0}
    return {"simulators": pool.status(), "size": pool.size,
            "available": pool.available}


# --- batch evaluation (spec section 61) ----------------------------------
@router.post("/batch", status_code=201, tags=["batch"])
def create_batch(payload: BatchIn, request: Request,
                 db: Session = Depends(get_session)):
    """Run every model over the same set of seeds.

    One episode is a sample of one. A model that passes a scenario on seed 42
    has told you almost nothing; the same model over twenty seeds tells you
    whether it drives.
    """
    if not payload.model_ids:
        raise HTTPException(400, "no models given")
    seeds = payload.seeds or list(range(payload.seed_start,
                                        payload.seed_start + payload.count))
    if not seeds:
        raise HTTPException(400, "no seeds given")

    try:
        experiments = [
            manager(request).create(db, model_id, payload.scenario_id, seed)
            for model_id in payload.model_ids
            for seed in seeds
        ]
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    manager(request).start_many(db, experiments)
    return {
        "scenario_id": payload.scenario_id,
        "seeds": seeds,
        "models": payload.model_ids,
        "experiments": [e.id for e in experiments],
    }


@router.get("/aggregate", tags=["batch"])
def aggregate(scenario_id: str, db: Session = Depends(get_session),
              seeds: str | None = None, experiments: str | None = None):
    """Per-model summary over completed episodes of a scenario.

    Pass `experiments` to summarise exactly one batch. Filtering by seed alone
    silently pulls in every earlier run that happened to use the same seed - a
    10-seed batch reported n=13 before this existed.
    """
    wanted_seeds = {int(s) for s in seeds.split(",")} if seeds else None
    wanted_ids = set(experiments.split(",")) if experiments else None

    rows = (db.query(Experiment, Episode)
              .join(Episode, Episode.experiment_id == Experiment.id)
              .filter(Experiment.scenario_id == scenario_id)
              .all())

    by_model: dict[str, list[Episode]] = {}
    for experiment, episode in rows:
        if wanted_ids is not None and experiment.id not in wanted_ids:
            continue
        if wanted_ids is None and wanted_seeds is not None \
                and experiment.seed not in wanted_seeds:
            continue
        by_model.setdefault(experiment.model_id, []).append(episode)

    def summarise(model_id: str, episodes: list[Episode]) -> dict:
        n = len(episodes)
        scores = sorted(e.score for e in episodes)
        ttcs = [e.minimum_ttc for e in episodes if e.minimum_ttc is not None]
        passes = sum(1 for e in episodes if e.result == "PASS")
        collisions = sum(1 for e in episodes if e.collision)
        return {
            "model_id": model_id,
            "episodes": n,
            # Rates, not counts: comparable across models with different
            # numbers of completed runs.
            "success_rate": passes / n,
            "collision_rate": collisions / n,
            "mean_score": mean(scores),
            "p95_score": percentile(scores, 95),
            "worst_score": scores[0],
            "mean_minimum_ttc": mean(ttcs) if ttcs else None,
            "mean_route_completion": mean([e.route_completion for e in episodes]),
            "mean_lane_invasions": mean([float(e.lane_invasion_count) for e in episodes]),
            "mean_latency_p50": mean([e.model_latency_p50 for e in episodes]),
            "mean_latency_p95": mean([e.model_latency_p95 for e in episodes]),
        }

    summaries = [summarise(m, e) for m, e in sorted(by_model.items())]
    # Best first, by success rate then mean score.
    summaries.sort(key=lambda s: (s["success_rate"], s["mean_score"]), reverse=True)
    return {"scenario_id": scenario_id, "models": summaries}
