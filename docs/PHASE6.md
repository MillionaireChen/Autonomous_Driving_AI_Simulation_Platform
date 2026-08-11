# Phase 6 - Backend

Goal: drive the platform over HTTP, with results in a database.

**Status: complete and passing.** Acceptance was "`POST /api/experiments`
creates and runs an experiment"; it does, end to end, and the run is
byte-identical to the same experiment launched from the command line.

## What was built

| Path | Purpose |
|---|---|
| `backend/database/models.py` | Schema: models, scenarios, experiments, episodes, events, metrics |
| `backend/database/session.py` | PostgreSQL lifecycle and engine |
| `backend/experiment_manager.py` | The state machine and the run thread |
| `backend/api/routes.py` | REST API (spec section 63) |
| `backend/schemas.py` | Request/response models |
| `backend/main.py` | App, plus registry sync from YAML on startup |
| `tests/test_experiment_manager.py` | 19 tests on the state machine |

## PostgreSQL installs through uv

No system package, no container, no second package manager. The `pgserver`
wheel bundles a full PostgreSQL 16.2 server, so `uv sync` is the whole setup:

```
uv add pgserver     ->  PostgreSQL 16.2, no root required
```

It listens on a **unix socket** inside its data directory rather than a TCP
port, so it cannot collide with anything else on this shared machine.

The data directory sits on local disk (`/var/tmp/fls/adarena/pgdata`). This is
the one deliberate exception to "everything on /home": PostgreSQL over NFS hits
fsync and file-locking problems and is discouraged upstream. Results, frames
and telemetry still go to /home. `DATABASE_URL` overrides all of this if you
would rather point at a server you already run.

## Design decisions

### One owner for `status`

Spec section 65 is explicit that no component may set experiment state at will,
so `ExperimentManager` is the only code that writes `Experiment.status`, and it
does so through a transition table:

```
CREATED -> STARTING -> RUNNING -> COMPLETED | FAILED | STOPPED
```

Anything not on that diagram raises `IllegalTransition`, which the API turns
into a 409 with the reason:

```
POST /api/experiments/EXP-0001/start   (already COMPLETED)
409  EXP-0001: COMPLETED -> STARTING is not allowed (legal: none, this state is terminal)
```

A status field anything can set is a status field nobody can trust.

### YAML stays the source of truth

`configs/models.yaml` and `scenarios/*.yaml` are synced into the database on
startup. The tables are a mirror that results can point at with a foreign key,
not a second place to edit configuration. The full scenario YAML is stored on
the row, so an old result stays interpretable after the file changes.

### Experiments run on a worker thread

An episode takes tens of seconds; the request that starts it returns
immediately with `STARTING`. Progress is observed by polling
`GET /api/experiments/{id}`.

### Stop is an override, not a request to the model

`POST /{id}/stop` sets a flag the worker checks every tick. On seeing it the
simulator applies throttle 0, brake 1 itself and ends the episode
(spec section 73). The model does not get a vote.

## A concurrency bug worth recording

The first stop test looked like it worked - the episode ended, the termination
reason said `STOPPED` - but the experiment finished as **`COMPLETED`**.

The stop arrives on the API's session and commits `STOPPED`. The worker thread
then finished its episode and asked its own session for the row to decide the
final state. SQLAlchemy's identity map returned the copy that thread had loaded
at the start of the run, still saying `RUNNING`, so `RUNNING -> COMPLETED` was
a legal transition and it overwrote the stop.

The fix is `db.expire_all()` before the final read, so the decision is made
against the database rather than a cache, and the first terminal state wins.
This is exactly the class of bug that a single-owner state machine is supposed
to prevent, and it still got in through a stale read.

## Acceptance results

Full run over HTTP, CARLA and the dummy model service both live:

```
POST /api/experiments  {"model_id":"dummy","scenario_id":"highway_cut_in_001","seed":42}
  201  EXP-0001  status=CREATED

POST /api/experiments/EXP-0001/start
  200  status=STARTING
       ... RUNNING ... (polled)
  after ~20 s: COMPLETED, score 0.0

GET /api/experiments/EXP-0001
  versions: {git_commit: 9ad9263, carla_client: 0.9.16,
             carla_server: 0.9.16, scenario_version: "1.0"}
```

```
GET /api/experiments/EXP-0001/episodes
  collision=true  minimum_ttc=4.548  route_completion=82.00%
  lane_invasions=14  ticks=672  distance=253.58 m
  model_latency p50=1.505 ms p95=2.226 ms  result=FAIL  score=0.0

GET /api/experiments/EXP-0001/events
    0.00s  EPISODE_STARTED    {"scenario": "highway_cut_in_001", "seed": 42}
    6.05s  CUT_IN_TRIGGERED   {"side": "right", "gap_m": 12.15}
    8.05s  CUT_IN_COMPLETED   {"gap_m": 30.7}
   33.55s  COLLISION          {"other_actor": "static.guardrail"}
```

**The API path and the CLI path agree exactly**: 672 ticks, 253.58 m, 82.00%
route, cut-in at 6.05 s and a 12.15 m gap - the same numbers Phase 4 and 5
produced from the command line. Only latency differs, because the model is now
reached over gRPC.

Also verified:

- **Stop works and sticks**: a running experiment stopped mid-episode ends
  `STOPPED`, with `termination_reason=STOPPED` on the episode row.
- **Guards return the right codes**: unknown model 404, unknown scenario 404,
  duplicate model registration 409, illegal transition 409.
- **Provenance is recorded** on every experiment: git commit, CARLA client and
  server versions, scenario version and seed (spec section 75).
- **120 unit tests pass**, 19 new.

## Known issues

1. **`frames` table not created.** Spec section 38 lists it, but nothing writes
   frames yet - the dataset recorder is Phase 9. An empty table would be a
   placeholder, so it arrives with its writer.
2. **`hard_brake_count` on the episode row is always 0.** The value lives in
   metrics.json and in the flat `metrics` table; the dedicated column is not
   wired to it yet.
3. **Tests run against SQLite, not PostgreSQL.** The schema is portable and it
   keeps the suite at ~2 s. PostgreSQL is covered by actually running the API,
   as recorded above, but a Postgres-backed test would be stronger.
4. **One experiment at a time in practice.** Nothing enforces it, but there is
   a single CARLA server, so two concurrent experiments would fight over it.
   The multi-simulator manager is Phase 13.
5. **No WebSocket yet.** Progress is polled. Live telemetry streaming is
   Phase 7, with the dashboard.

## Not in this phase

No frontend. The API is the deliverable; `http://127.0.0.1:8000/docs` is the
only interface so far.
