"""Tests for the experiment state machine and the API surface.

The state machine is the part that must not be wrong: spec section 65 makes the
experiment manager the sole owner of `status`, so an illegal transition has to
be refused rather than quietly applied.

These run against SQLite rather than the bundled PostgreSQL - the schema is
portable and it keeps the suite fast. PostgreSQL is exercised by actually
running the API, which is recorded in docs/PHASE6.md.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Experiment, Model, Scenario
from backend.experiment_manager import TERMINAL, TRANSITIONS, ExperimentManager, IllegalTransition


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(sessions):
    session = sessions()
    session.add(Model(id="dummy", name="Dummy", endpoint="localhost:51001"))
    session.add(Scenario(id="sc-1", name="Test", map="Town04", source="highway_cut_in"))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def manager(sessions, tmp_path):
    return ExperimentManager(sessions, output_root=tmp_path / "out")


class TestTransitionTable:
    def test_the_happy_path_is_reachable(self):
        assert "STARTING" in TRANSITIONS["CREATED"]
        assert "RUNNING" in TRANSITIONS["STARTING"]
        assert "COMPLETED" in TRANSITIONS["RUNNING"]

    def test_terminal_states_lead_nowhere(self):
        for state in TERMINAL:
            assert TRANSITIONS[state] == set(), f"{state} should be terminal"

    def test_a_run_cannot_be_skipped(self):
        """CREATED must not jump straight to RUNNING or COMPLETED."""
        assert "RUNNING" not in TRANSITIONS["CREATED"]
        assert "COMPLETED" not in TRANSITIONS["CREATED"]

    def test_only_a_started_experiment_can_fail(self):
        assert "FAILED" not in TRANSITIONS["CREATED"]
        assert "FAILED" in TRANSITIONS["STARTING"]
        assert "FAILED" in TRANSITIONS["RUNNING"]

    def test_stop_is_only_possible_while_running(self):
        assert "STOPPED" in TRANSITIONS["RUNNING"]
        assert "STOPPED" not in TRANSITIONS["CREATED"]
        assert "STOPPED" not in TRANSITIONS["STARTING"]


class TestTransitions:
    def test_legal_transition_is_applied(self, manager, db):
        experiment = manager.create(db, "dummy", "sc-1", seed=42)
        manager.set_status(db, experiment, "STARTING")
        assert experiment.status == "STARTING"

    def test_illegal_transition_is_refused(self, manager, db):
        experiment = manager.create(db, "dummy", "sc-1", seed=42)
        with pytest.raises(IllegalTransition):
            manager.set_status(db, experiment, "COMPLETED")
        assert experiment.status == "CREATED"

    def test_a_terminal_experiment_cannot_restart(self, manager, db):
        experiment = manager.create(db, "dummy", "sc-1", seed=42)
        for state in ("STARTING", "RUNNING", "COMPLETED"):
            manager.set_status(db, experiment, state)
        with pytest.raises(IllegalTransition, match="terminal"):
            manager.set_status(db, experiment, "STARTING")

    def test_timestamps_are_stamped_at_the_right_moments(self, manager, db):
        experiment = manager.create(db, "dummy", "sc-1", seed=42)
        assert experiment.started_at is None and experiment.finished_at is None

        manager.set_status(db, experiment, "STARTING")
        assert experiment.started_at is None

        manager.set_status(db, experiment, "RUNNING")
        assert experiment.started_at is not None and experiment.finished_at is None

        manager.set_status(db, experiment, "COMPLETED")
        assert experiment.finished_at is not None


class TestCreate:
    def test_ids_are_sequential(self, manager, db):
        assert manager.create(db, "dummy", "sc-1", 1).id == "EXP-0001"
        assert manager.create(db, "dummy", "sc-1", 2).id == "EXP-0002"

    def test_the_seed_is_recorded(self, manager, db):
        assert manager.create(db, "dummy", "sc-1", seed=1234).seed == 1234

    def test_the_git_commit_is_recorded(self, manager, db):
        """Every result must be traceable to the code that produced it."""
        experiment = manager.create(db, "dummy", "sc-1", 42)
        assert "git_commit" in experiment.versions

    def test_an_unknown_model_is_rejected(self, manager, db):
        with pytest.raises(LookupError, match="model"):
            manager.create(db, "ghost", "sc-1", 42)

    def test_an_unknown_scenario_is_rejected(self, manager, db):
        with pytest.raises(LookupError, match="scenario"):
            manager.create(db, "dummy", "ghost", 42)

    def test_a_new_experiment_starts_in_created(self, manager, db):
        assert manager.create(db, "dummy", "sc-1", 42).status == "CREATED"


class TestStop:
    def test_stopping_before_the_episode_begins_fails_it(self, manager, db):
        """STARTING means nothing is driving yet, so there is nothing to stop."""
        experiment = manager.create(db, "dummy", "sc-1", 42)
        manager.set_status(db, experiment, "STARTING")
        manager.stop(db, experiment)
        assert experiment.status == "FAILED"
        assert "stopped before" in experiment.error

    def test_stopping_a_running_experiment_marks_it_stopped(self, manager, db):
        experiment = manager.create(db, "dummy", "sc-1", 42)
        manager.set_status(db, experiment, "STARTING")
        manager.set_status(db, experiment, "RUNNING")
        manager.stop(db, experiment)
        assert experiment.status == "STOPPED"

    def test_stopping_a_finished_experiment_is_a_no_op(self, manager, db):
        experiment = manager.create(db, "dummy", "sc-1", 42)
        for state in ("STARTING", "RUNNING", "COMPLETED"):
            manager.set_status(db, experiment, state)
        manager.stop(db, experiment)
        assert experiment.status == "COMPLETED"


class TestPersistenceShape:
    def test_experiments_survive_a_round_trip(self, manager, db, sessions):
        experiment = manager.create(db, "dummy", "sc-1", seed=99)
        with sessions() as other:
            loaded = other.get(Experiment, experiment.id)
            assert loaded.seed == 99
            assert loaded.model_id == "dummy"
            assert loaded.scenario_id == "sc-1"
