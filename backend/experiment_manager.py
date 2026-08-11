"""The experiment manager: the only thing allowed to change experiment state.

Spec section 65 defines the state machine and insists on a single owner:

    CREATED -> STARTING -> RUNNING -> COMPLETED
                                   -> FAILED
                                   -> STOPPED

Nothing else in the codebase writes `Experiment.status`. Transitions that are
not on the diagram are rejected rather than silently applied, because a status
field that anything can set is a status field nobody can trust.

An experiment runs on a worker thread: an episode takes tens of seconds and the
HTTP request that starts it must return immediately.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import Episode, Event, Experiment, Metric, Model, Scenario
from simulator.stream import Broadcaster

REPO_ROOT = Path(__file__).resolve().parent.parent

# The only legal moves (spec section 65).
TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"STARTING"},
    "STARTING": {"RUNNING", "FAILED"},
    "RUNNING": {"COMPLETED", "FAILED", "STOPPED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "STOPPED": set(),
}

TERMINAL = {"COMPLETED", "FAILED", "STOPPED"}


class IllegalTransition(RuntimeError):
    pass


def git_commit() -> str:
    """Recorded with every result so a number can be traced back to code."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


class ExperimentManager:
    def __init__(self, sessions: sessionmaker, output_root: Optional[Path] = None):
        self.sessions = sessions
        self.output_root = output_root or (REPO_ROOT / "output" / "experiments")
        self._threads: dict[str, threading.Thread] = {}
        self._stop_flags: dict[str, threading.Event] = {}
        #: Live telemetry fan-out, one per experiment (spec sections 53/54).
        self.streams: dict[str, Broadcaster] = {}
        self._lock = threading.Lock()

    # -- state ------------------------------------------------------------
    @staticmethod
    def _transition(experiment: Experiment, new_status: str) -> None:
        allowed = TRANSITIONS.get(experiment.status, set())
        if new_status not in allowed:
            raise IllegalTransition(
                f"{experiment.id}: {experiment.status} -> {new_status} is not allowed "
                f"(legal: {sorted(allowed) or 'none, this state is terminal'})"
            )
        experiment.status = new_status

    def set_status(self, db: Session, experiment: Experiment, status: str) -> None:
        self._transition(experiment, status)
        now = datetime.now(timezone.utc)
        if status == "RUNNING":
            experiment.started_at = now
        if status in TERMINAL:
            experiment.finished_at = now
        db.commit()

    # -- lifecycle --------------------------------------------------------
    def create(self, db: Session, model_id: str, scenario_id: str, seed: int) -> Experiment:
        if db.get(Model, model_id) is None:
            raise LookupError(f"unknown model {model_id!r}")
        if db.get(Scenario, scenario_id) is None:
            raise LookupError(f"unknown scenario {scenario_id!r}")

        experiment = Experiment(
            id=self._next_id(db),
            model_id=model_id,
            scenario_id=scenario_id,
            seed=seed,
            status="CREATED",
            versions={"git_commit": git_commit()},
        )
        db.add(experiment)
        db.commit()
        return experiment

    @staticmethod
    def _next_id(db: Session) -> str:
        count = db.query(Experiment).count()
        return f"EXP-{count + 1:04d}"

    def start(self, db: Session, experiment: Experiment) -> None:
        self.set_status(db, experiment, "STARTING")
        stop_flag = threading.Event()
        thread = threading.Thread(
            target=self._run, args=(experiment.id, stop_flag),
            name=f"experiment-{experiment.id}", daemon=True,
        )
        with self._lock:
            self._threads[experiment.id] = thread
            self._stop_flags[experiment.id] = stop_flag
            self.streams[experiment.id] = Broadcaster()
        thread.start()

    def stop(self, db: Session, experiment: Experiment) -> None:
        """Ask a running experiment to stop at the next tick."""
        with self._lock:
            flag = self._stop_flags.get(experiment.id)
        if flag is not None:
            flag.set()
        if experiment.status in ("STARTING", "RUNNING"):
            if experiment.status == "STARTING":
                # Never reached RUNNING; nothing is driving, so fail it out.
                self.set_status(db, experiment, "FAILED")
                experiment.error = "stopped before the episode began"
                db.commit()
            else:
                self.set_status(db, experiment, "STOPPED")

    def is_running(self, experiment_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(experiment_id)
        return thread is not None and thread.is_alive()

    # -- the run ----------------------------------------------------------
    def _run(self, experiment_id: str, stop_flag: threading.Event) -> None:
        """Execute the episode. Runs on its own thread with its own session."""
        # Imported here so the API can be served on a machine without CARLA.
        from simulator import config as cfg
        from simulator.scenario import load_scenario
        from simulator.worker import SimulationWorker

        db: Session = self.sessions()
        try:
            experiment = db.get(Experiment, experiment_id)
            model = db.get(Model, experiment.model_id)
            scenario_row = db.get(Scenario, experiment.scenario_id)

            sim_config = cfg.load_simulator_config()
            camera_config = cfg.load_camera_config()
            ego_config = cfg.load_yaml("simulator/ego.yaml")
            episode_config = cfg.load_episode_config()
            evaluation_config = cfg.load_yaml("evaluation.yaml")

            scenario = load_scenario(scenario_row.source or scenario_row.id)
            scenario.seed = experiment.seed

            from model_gateway.adapters.remote import RemoteModelAdapter

            policy = RemoteModelAdapter(
                endpoint=model.endpoint, timeout_ms=model.timeout_ms
            )

            self.set_status(db, experiment, "RUNNING")

            worker = SimulationWorker(
                sim_config, camera_config, ego_config, episode_config,
                evaluation_config=evaluation_config,
            )
            output_dir = self.output_root / experiment.id
            stream = self.streams.get(experiment_id)
            result = worker.run_episode(
                policy, episode_id=experiment.id,
                output_dir=output_dir, scenario=scenario,
                stop_flag=stop_flag,
                on_tick=stream.publish if stream is not None else None,
            )

            self._persist(db, experiment, result, output_dir)

            # Re-read from the database, do not trust the identity map. A stop
            # arrives on the API's session, so this thread's cached copy still
            # says RUNNING and would overwrite STOPPED with COMPLETED.
            db.expire_all()
            experiment = db.get(Experiment, experiment_id)
            if experiment.status in TERMINAL:
                # Something already ended it - a stop, almost certainly. The
                # first terminal state wins.
                experiment.score = result.score
                db.commit()
            else:
                experiment.score = result.score
                self.set_status(db, experiment, "COMPLETED")

        except Exception as exc:
            db.rollback()
            db.expire_all()
            experiment = db.get(Experiment, experiment_id)
            if experiment is not None and experiment.status not in TERMINAL:
                experiment.error = f"{type(exc).__name__}: {exc}"
                experiment.status = "FAILED"
                experiment.finished_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
            stream = self.streams.get(experiment_id)
            if stream is not None:
                stream.close()
            with self._lock:
                self._threads.pop(experiment_id, None)
                self._stop_flags.pop(experiment_id, None)

    @staticmethod
    def _persist(db: Session, experiment: Experiment, result, output_dir: Path) -> None:
        versions = dict(experiment.versions or {})
        versions.update(result.versions or {})
        experiment.versions = versions

        db.add(Episode(
            experiment_id=experiment.id,
            collision=result.collisions > 0,
            collision_count=result.collisions,
            minimum_ttc=result.minimum_ttc_s,
            route_completion=result.route_completion_percent or 0.0,
            average_speed=result.average_speed_mps,
            max_speed=result.max_speed_mps,
            hard_brake_count=0,
            lane_invasion_count=result.lane_invasions,
            model_latency_p50=result.inference_latency_ms_p50,
            model_latency_p95=result.inference_latency_ms_p95,
            model_timeouts=result.model_timeouts,
            ticks=result.ticks,
            duration_s=result.simulated_seconds,
            distance_m=result.distance_m,
            termination_reason=result.termination_reason,
            result=result.result or "UNKNOWN",
            score=result.score or 0.0,
            artifacts_path=str(output_dir),
        ))

        for event in result.events or []:
            db.add(Event(
                experiment_id=experiment.id,
                time_s=float(event.get("time", 0.0)),
                type=str(event.get("type", "")),
                data=event.get("data", {}),
            ))

        # Flatten metrics.json so results are queryable and comparable.
        metrics_file = output_dir / "metrics.json"
        if metrics_file.exists():
            import json

            for name, value in json.loads(metrics_file.read_text()).items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                db.add(Metric(experiment_id=experiment.id, name=name, value=float(value)))
        db.commit()
