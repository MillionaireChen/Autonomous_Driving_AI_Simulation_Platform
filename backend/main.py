"""FastAPI application.

On startup the registry files (configs/models.yaml, scenarios/*.yaml) are
synced into the database, so the API and the command line always agree about
what exists. The YAML files stay the source of truth; the tables are a mirror
that results can point at with a foreign key.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from backend.api.routes import router  # noqa: E402
from backend.database.models import Model, Scenario  # noqa: E402
from backend.database.session import create_all, session_factory  # noqa: E402
from backend.experiment_manager import ExperimentManager  # noqa: E402

SCENARIO_DIR = REPO_ROOT / "scenarios"
MODELS_YAML = REPO_ROOT / "configs" / "models.yaml"


def sync_registries() -> tuple[int, int]:
    """Mirror models.yaml and scenarios/*.yaml into the database."""
    sessions = session_factory()
    models = scenarios = 0
    with sessions() as db:
        if MODELS_YAML.exists():
            with MODELS_YAML.open() as fh:
                for entry in (yaml.safe_load(fh) or {}).get("models", []):
                    row = db.get(Model, entry["id"]) or Model(id=entry["id"])
                    row.name = entry.get("name", entry["id"])
                    row.type = entry.get("type", "CONTROL_POLICY")
                    row.endpoint = entry["endpoint"]
                    row.timeout_ms = int(entry.get("timeout_ms", 500))
                    row.gpu = entry.get("gpu")
                    db.merge(row)
                    models += 1

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
    create_all()
    models, scenarios = sync_registries()
    app.state.manager = ExperimentManager(session_factory())
    print(f"registry synced: {models} model(s), {scenarios} scenario(s)", flush=True)
    yield


app = FastAPI(
    title="Autonomous Driving AI Arena",
    description="Closed-loop simulation platform for autonomous driving models.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
