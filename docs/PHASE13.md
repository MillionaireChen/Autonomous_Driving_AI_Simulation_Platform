# Phase 13 - Parallel simulation

Goal: run two episodes at once, on two CARLA servers, without either affecting
the other.

**Status: complete and passing.**

## What was built

| Path | Purpose |
|---|---|
| `simulator/pool.py` | `SimulatorPool` - lease a server for an episode |
| `configs/simulator/simulators.yaml` | The pool, one entry per server |
| `scripts/carla_server.sh` | Multi-instance: per-port pid file, log and GPU |
| `backend/api/routes.py` | `GET /api/simulators` |
| `tests/test_pool.py` | 11 tests on leasing |

## Design decisions

### Parallelism is a property of the pool, not a code path

`start_many()` gives every experiment its own thread, and each thread leases a
server. How many actually run at once is decided by how many servers exist. One
entry and they queue; two and they overlap. There is no `if parallel:` anywhere,
which matters because a branch that only runs when someone remembers to
configure two simulators is a branch that is usually broken.

This replaced the sequential arena runner from Phase 12, which existed only
because there was one server.

### Leasing is a correctness requirement, not an optimisation

Two episodes sharing one CARLA in synchronous mode would interleave their world
ticks. Neither would report an error; both would simply be wrong. The lease
makes that impossible, and the timeout turns an exhausted pool into a clear
`NoSimulatorAvailable` rather than a hang.

The lease is released in a `finally`, so an episode that raises does not
permanently remove a server from the pool. A test covers exactly that.

### Both servers are on GPU 0

Spec section 62 puts the second instance on GPU 2. On this machine GPUs 1-3 are
running other people's jobs and GPU 0 is the only free card. Two CARLA servers
cost about 5 GB each out of 97 GB, so they share rendering throughput rather
than memory. The GPU is per-entry in the config, so a machine with free cards
needs a config change and no code change.

### Ports are 10 apart, not 1

CARLA binds `port+1` and `port+2` for streaming as well as its RPC port, so
instances must be at least three apart. 2000 and 2010. A test asserts the
shipped config keeps that spacing, because the failure mode otherwise is a
second server that half-starts.

## Acceptance results

Two experiments started together, polled every 4 seconds alongside the pool:

```
t+4s   A=STARTING   B=STARTING   pool=2/2 free
t+8s   A=RUNNING    B=RUNNING    pool=0/2 free
...
t+36s  A=COMPLETED  B=COMPLETED  pool=2/2 free
```

Both `RUNNING` at once with the pool exhausted, then both servers returned.
They landed on different servers, which the experiment record now carries:

```
EXP-0014 pid     simulator=carla-0
EXP-0015 dummy   simulator=carla-1
```

**Parallel execution does not change the results.** The same match that ran
sequentially in Phase 12, re-run with both episodes in parallel:

| | sequential (Phase 12) | parallel (Phase 13) |
|---|---|---|
| pid score | 85.62 | **85.62** |
| pid distance | 359.21 m | **359.21 m** |
| pid route | 58.10% | **58.10%** |

Identical to the last digit, which is the claim worth making: sharing a GPU
between two renderers has not perturbed the physics.

The learned model came out slightly different (41.30% vs 42.33% route, 254.56 m
vs 260.06 m). That is not the pool: it is a failing run whose small deviations
compound, and Phase 11 documented why it drifts. The deterministic controller is
the one to check for interference, and it did not move.

`make test`: **147 passed**.

## Known issues

1. **The pool is not aware of whether a server is actually up.** It leases
   endpoints from a config file; if that CARLA is not running, the episode
   fails with a connection error rather than the pool skipping it. A health
   check at startup would be better.
2. **Two servers on one GPU share rendering throughput.** Wall-clock per
   episode goes up slightly under contention even though results do not change.
3. **No autoscaling and no supervision.** Servers are started by hand with
   `./scripts/carla_server.sh start 2010 0`; nothing restarts one that dies
   mid-experiment.
4. **The pool is per-process.** Two API processes would each believe they own
   every server. A single backend owns the pool, which is fine here and would
   not survive being scaled out.
