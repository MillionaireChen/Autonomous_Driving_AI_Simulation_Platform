"""Database connection.

PostgreSQL comes from the `pgserver` wheel, so `uv sync` installs the server
itself and no system package, container or second package manager is involved.
It listens on a unix socket inside its data directory rather than a TCP port,
which also means it cannot collide with anything else running on this shared
machine.

The data directory lives on local disk: PostgreSQL on NFS hits fsync and file
locking problems and is explicitly discouraged upstream. Everything the
platform *produces* still goes to /home; this is a runtime, like the venv.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import Base

DEFAULT_PGDATA = Path(
    os.environ.get("ARENA_PGDATA", "/var/tmp/fls/adarena/pgdata")
)

_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None
_server = None  # kept alive for the lifetime of the process


def database_url() -> str:
    """Explicit DATABASE_URL wins; otherwise start the bundled server."""
    if url := os.environ.get("DATABASE_URL"):
        return url

    import pgserver

    global _server
    DEFAULT_PGDATA.mkdir(parents=True, exist_ok=True)
    _server = pgserver.get_server(DEFAULT_PGDATA)
    # pgserver hands back a psycopg2-style URL; this project uses psycopg 3.
    return _server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        # Episode threads hold a connection for the length of a run, so the
        # default 5+10 is easily exhausted by a batch. Waiting threads hold
        # nothing (see ExperimentManager._run), but headroom is cheap.
        _engine = create_engine(
            database_url(), pool_pre_ping=True, future=True,
            pool_size=20, max_overflow=20, pool_timeout=60,
        )
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def create_all() -> None:
    Base.metadata.create_all(get_engine())


def migrate() -> str:
    """Bring the database up to date, whether it is new or pre-existing.

    A fresh database is built straight from the models and stamped at head - no
    point replaying history to arrive at the schema we already have. An
    existing one is upgraded through Alembic, so results recorded by earlier
    phases survive a schema change instead of being dropped.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    engine = get_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    config = Config(str(Path(__file__).resolve().parent.parent.parent / "alembic.ini"))
    config.set_main_option("script_location", "backend/migrations")

    if not tables:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        return "created"

    if "alembic_version" not in tables:
        # Predates migrations: mark it as the baseline, then upgrade.
        command.stamp(config, "0001")
    command.upgrade(config, "head")
    return "upgraded"


def session_factory() -> sessionmaker:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def get_session() -> Session:
    """FastAPI dependency: one session per request."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()
