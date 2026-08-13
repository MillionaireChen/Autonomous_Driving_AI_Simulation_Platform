"""FastAPI application.

On startup the registry files (configs/models.yaml, scenarios/*.yaml) are
synced into the database, so the API and the command line always agree about
what exists. The YAML files stay the source of truth; the tables are a mirror
that results can point at with a foreign key.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from backend.api.routes import router  # noqa: E402
from backend.api.websocket import router as ws_router  # noqa: E402
from backend.database.models import Model, Scenario  # noqa: E402
from backend.database.session import migrate, session_factory  # noqa: E402
from backend.experiment_manager import ExperimentManager  # noqa: E402
from simulator import config as simcfg  # noqa: E402
from simulator.pool import SimulatorPool  # noqa: E402

SCENARIO_DIR = REPO_ROOT / "scenarios"
MODELS_YAML = REPO_ROOT / "configs" / "models.yaml"


def sync_registries() -> tuple[int, int]:
    """Mirror models.yaml and scenarios/*.yaml into the database."""
    sessions = session_factory()
    models = scenarios = 0
    with sessions() as db:
        if MODELS_YAML.exists():
            with MODELS_YAML.open() as fh:
                registered = set()
                for entry in (yaml.safe_load(fh) or {}).get("models", []):
                    row = db.get(Model, entry["id"]) or Model(id=entry["id"])
                    row.name = entry.get("name", entry["id"])
                    row.type = entry.get("type", "CONTROL_POLICY")
                    row.endpoint = entry["endpoint"]
                    row.timeout_ms = int(entry.get("timeout_ms", 500))
                    row.gpu = entry.get("gpu")
                    row.display_order = int(entry.get("display_order", 100))
                    row.archived = False
                    db.merge(row)
                    registered.add(entry["id"])
                    models += 1

                # Anything the YAML no longer lists is retired, not deleted:
                # its experiments still point at it. Without this the registry
                # only ever grew, and the dashboard went on offering models
                # whose services had been shut down for good.
                for row in db.query(Model).filter(Model.id.notin_(registered)):
                    row.archived = True

        for path in sorted(SCENARIO_DIR.glob("*.yaml")):
            with path.open() as fh:
                data = yaml.safe_load(fh) or {}
            if "id" not in data:
                continue
            row = db.get(Scenario, data["id"]) or Scenario(id=data["id"])
            row.name = data.get("name", data["id"])
            row.map = data.get("map", "")
            row.version = str(data.get("version", "1.0"))
            row.duration_seconds = float(data.get("duration_seconds", 40))
            row.default_seed = int(data.get("seed", 42))
            row.source = path.stem
            row.definition = data
            db.merge(row)
            scenarios += 1
        db.commit()
    return models, scenarios


@asynccontextmanager
async def lifespan(app: FastAPI):
    action = migrate()
    models, scenarios = sync_registries()
    print(f"database {action}", flush=True)
    pool = SimulatorPool.from_config(simcfg.load_yaml("simulator/simulators.yaml"))
    app.state.manager = ExperimentManager(session_factory(), pool=pool)
    print(f"registry synced: {models} model(s), {scenarios} scenario(s)", flush=True)
    print(f"simulator pool: {', '.join(str(e) for e in pool.endpoints)}", flush=True)
    yield


app = FastAPI(
    title="Autonomous Driving AI Arena",
    description="Closed-loop simulation platform for autonomous driving models.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(ws_router)

# The dashboard is served from a different origin during development.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# The dashboard runs on a different origin, and on a different host when the
# demo is watched from another machine. ARENA_CORS_ORIGINS is a comma-separated
# list; the loopback origins are always allowed.
_extra = [o.strip() for o in os.environ.get("ARENA_CORS_ORIGINS", "").split(",")
          if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", *_extra],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
