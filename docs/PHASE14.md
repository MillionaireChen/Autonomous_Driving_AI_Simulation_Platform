# Phase 14 - Batch evaluation

Goal: stop drawing conclusions from single episodes.

**Status: complete and passing.**

## What was built

| Path | Purpose |
|---|---|
| `backend/api/routes.py` | `POST /api/batch`, `GET /api/aggregate` |
| `scripts/batch_eval.py` | Run the sweep and print the leaderboard |
| `scenarios/highway_cut_in.yaml` | Per-seed randomisation ranges |
| `simulator/scenario.py` | Seeded sampling of those ranges |
| `tests/test_scenario.py` | 6 tests on the randomisation |

## The first run measured nothing, and looked great doing it

The first 30-episode sweep produced this:

```
model        n  success  collision    mean    p95  worst
pid         10     100%         0%    85.6   85.6   85.6
```

Mean, p95 and worst identical to one decimal place. That is not a robust model,
it is the **same episode ten times**. The seed was threaded through the whole
stack and changed nothing: it only fed the scenario runner's RNG, which was
used for background traffic, and background traffic is off.

A sweep of identical runs is worse than no sweep, because it reports
confidence it has not earned.

`scenarios/highway_cut_in.yaml` now declares what varies, and the runner
samples it deterministically from the seed (spec section 29):

```yaml
randomization:
  scenario_vehicle:
    initial_longitudinal_distance_m: [-40.0, -15.0]
    speed_mps: [19.0, 24.0]
  action:
    duration_seconds: [1.4, 2.6]
    speed_after_mps: [5.0, 11.0]
  trigger:
    distance_m: [8.0, 16.0]
```

Same seed, same episode; different seed, genuinely different one. Tests pin
both directions, and that the values stay inside their bounds.

The very next two-seed run showed the difference immediately: the PID scored
**82.6 on one seed and 0.0 on the other** - a failure mode that ten identical
episodes had hidden completely.

## Two more bugs the batch exposed

**The aggregate pulled in other people's runs.** A 10-seed batch reported
`n=13` for one model, because it summarised by scenario and seed and every
earlier experiment that happened to use those seeds matched. `/api/aggregate`
now takes an explicit experiment list, and `batch_eval.py` passes exactly the
ids it created.

**Thirty threads exhausted the database pool.** Starting 30 experiments raised:

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00
```

Each experiment thread opened a session immediately and held it while queueing
for a simulator - but only two can run at once, so 28 threads sat on a
connection doing nothing for minutes. `_run` now reads what it needs with a
short-lived session, releases it, and only opens a working session once it
actually holds a simulator lease. Waiting threads hold nothing.

### A silent one: the API had stopped logging errors

Diagnosing that took longer than it should have, because the 500 appeared with
**no traceback and no access log at all**. Alembic's `fileConfig()` defaults to
`disable_existing_loggers=True`, and migrations run inside application startup -
so it switched off uvicorn's access and error loggers on every boot. Every
unhandled exception since Phase 9 had been vanishing.

Fixed with `disable_existing_loggers=False`. Worth remembering: a logging
config that runs after another component has configured logging will silence it
by default.

## Acceptance results

Three models, ten randomised seeds, both simulators:

```
$ uv run python scripts/batch_eval.py --models pid cnn_il dummy --count 10
30 episodes (3 models x 10 seeds) across 2 simulator(s)
  30/30 finished, 0 running, 441s elapsed

model        n  success  collision    mean    p95  worst  meanTTC  route%  lane  p50 ms
---------------------------------------------------------------------------------------
pid         10     100%         0%    84.9   89.0   71.5     5.88    59.2   0.0    0.98
cnn_il      10       0%       100%     0.0    0.0    0.0     4.43    43.4  11.2   10.85
dummy       10       0%       100%     0.0    0.0    0.0    19.43    39.5  12.6    0.97

30 episodes in 7.3 min -> output/batch/leaderboard.json
```

Two episodes ran continuously for the whole 441 s - the pool never idled.

The PID's spread is the point: **mean 84.9, p95 89.0, worst 71.5**. A single
episode reported 85.6 and told you nothing about the 71.5. Its `dummy` and
`cnn_il` counterparts collide on every seed, which single runs had already
suggested and this confirms as a rate rather than an anecdote.

`make test`: **152 passed**.

## Known issues

1. **The leaderboard is one scenario.** A model that handles Highway Cut-In has
   not been shown to drive; it has been shown to handle one manoeuvre family.
2. **Ten seeds is a small sample.** The rates above have wide confidence
   intervals, and no interval is reported.
3. **An earlier two-seed run found a PID failure (score 0.0) that seeds 601-610
   did not reproduce.** The rule-based expert is not robust across the whole
   randomisation range, and this batch happened not to sample the bad region.
   That is exactly the argument for more seeds, and it is not resolved here.
4. **No web view of the leaderboard.** It is a CLI table and a JSON file; the
   arena page still shows single matches.
5. **Failed experiments are excluded** rather than counted as failures - a model
   whose service crashes gets a smaller `n`, not a worse rate.
