# Phase 10 - PID / rule-based baseline

Goal: something that can actually drive, so the evaluation engine has more to
measure than a car leaving the road.

**Status: complete and passing.** Same scenario, same seed, two models:

| | result | score | collisions | lane invasions | min TTC | hard brakes | route |
|---|---|---|---|---|---|---|---|
| DummyAgent | **FAIL** | 0.0 | 1 | 14 | 17.97 s | 0 | 40.6% |
| PIDAgent | **PASS** | 85.6 | 0 | 0 | 7.06 s | 2 | 58.1% |

That contrast is the demo (spec section 79): nothing about the scenario
changed, only the model, and the outcome inverted.

## What was built

| Path | Purpose |
|---|---|
| `models/pid/policy.py` | Pure pursuit + IDM car-following |
| `models/pid/service.py` | The same policy as a gRPC service |
| `simulator/types.py` | `route_waypoints` and `LeadVehicle` on the observation |
| `simulator/route.py` | `Route.upcoming()` |
| `simulator/worker.py` | Populates both; `route_completed` termination |

## Design decisions

### A driving policy is given a route

A lane-follower needs to know where the lane goes, and the policy cannot touch
CARLA. So the observation carries `route_waypoints`: the next 80 m of the route
the simulator already builds for scoring. Every real driving stack is given a
route; inferring where to go from pixels alone is a different problem.

### Privileged information is opt-in and visible

`LeadVehicle` is ground truth about the car in front. A camera-only model must
infer that and must not be handed it, so it is only sent to a policy that
declares `lead_vehicle` in `required_sensors` - which the sensor gating from
Phase 3 already enforces. The PID declares `("route", "lead_vehicle", "speed")`
and reads no pixels at all. That is what a baseline is for: it establishes what
the scenario looks like driven competently, not how hard perception is.

It uses the same lateral corridor as the TTC metric, so the model and the score
agree about what counts as "in front".

### IDM, after two worse attempts

The longitudinal controller went through three versions, and the measurements
are worth keeping:

| controller | hard brakes | max speed | score |
|---|---|---|---|
| PI with a hard TTC cutoff | 5 | 15.0 | 77.0 |
| ...with smooth scaling and a deadband | 7 | 14.9 | 71.1 |
| IDM | 2 | 15.0 | 85.6 |

The hard cutoff chattered: below the TTC threshold it demanded zero speed, so
it braked fully, cleared the threshold, went to full throttle, and tripped it
again. Softening that made it *worse*, because gentle scaling let the car creep
closer and brake repeatedly instead of decisively once.

The Intelligent Driver Model has neither failure mode. One continuous
acceleration accounts for the speed error and the gap together, so approaching
a slower vehicle is a single smooth deceleration into a steady following
distance.

### Throttle is not acceleration

IDM outputs an acceleration. Feeding it straight to the throttle pedal capped
the car at **10 m/s** against a 15 m/s target, because holding a speed takes
throttle just to balance drag - the throttle implied by a small acceleration is
not enough to maintain speed, let alone gain it.

The fix is a feedforward term, measured rather than guessed: an earlier run
held 14.4 m/s at throttle 0.51, so 0.035 throttle per m/s. IDM's acceleration
is then a correction on top of it.

Throttle and brake are also one continuous command through zero, rather than
two branches - see below for why.

## The metric was wrong, and the controller was being blamed for it

The PID kept scoring badly on `hard_brake_count`, and twice I "fixed" the
controller and made it worse. The measurements said otherwise:

**Nine hard brakes were reported with no brake command above 0.6.** Deriving
acceleration from the speed trace confirmed the deceleration was real
(-5.10 m/s²) - but it occurred at brake 0.03. It was engine braking from
dropping the throttle to zero at 15 m/s.

That is a controller problem, and it is fixed by treating throttle and brake as
one command through zero, so gentle slowing eases the throttle instead of
slamming it shut.

**But grouping the excursions showed 9 counted events for 2 actual
manoeuvres.** Deceleration wanders either side of -4 m/s² during a single
brake, and a bare edge detector counts every crossing. That is a measurement
bug in the evaluation engine, not a driving fault.

The detector is now a Schmitt trigger: a hard brake begins below -4.0 m/s² and
does not end until deceleration eases above -2.5 m/s². The same episode then
reports **2**, which is what happened. Two tests pin this.

Worth stating plainly: this change raised the PID's score from 64.6 to 85.6. It
is a fix to a counter that was wrong, verified against the raw speed trace
before it was touched - not a threshold moved until the number looked better.

## A configuration bug that looked like a driving bug

The first PID run drove 353 m, completed 100% of its route, then collided with
40 lane invasions. The telemetry showed steering was smooth throughout (mean
0.006, max 0.071), so it was not weaving.

The route was 300 m long. At 23.1 s it ran out, `Route.upcoming()` returned
nothing, the controller had no steering target, and the car drifted off the
road. Everything after that - the invasions, the collision - was one missing
configuration value.

Two fixes: the route is now 600 m, long enough to outlast a 40 s episode at
15 m/s, and **finishing the route now ends the episode** (`route_completed`,
spec section 24). Driving past the end of your route is not something to keep
doing.

## The scenario needed a faster overtake

With the ego actually cruising at 15 m/s, a 17 m/s NPC closes at 1.5 m/s: the
overtake alone takes about 25 s of a 40 s episode, leaving no time for the ego
to respond. Raised to 22 m/s, the overtake completes in about 5 s. This is the
second time spec section 25's numbers have had to be adjusted to produce the
manoeuvre they describe; both changes are documented in the scenario YAML.

## Acceptance results

```
$ uv run python scripts/run_episode.py --model pid --scenario highway_cut_in
scenario highway_cut_in_001 (Highway Cut-In) map=Town04 seed=42
model requires sensors: route, lead_vehicle, speed

status              COMPLETED (TIMEOUT)
cut-in triggered    YES at 10.10s
collisions          0
ticks               800 (40.0s simulated in 30.6s wall)
distance            359.2 m
speed               avg 9.0 m/s, max 15.0 m/s
inference latency   p50 0.934 ms, p95 1.958 ms

minimum TTC         7.06 s
lane invasions      0
route completion    58.1%
RESULT              PASS   score 85.6 / 100
```

The braking response, from an earlier tuning run where it was sharpest:

```
first brake at t=13.60 s   (cut-in triggered at 10.25 s)
hardest brake 1.00 at t=14.20 s, gap 25.6 m, TTC 3.578 s
speed 14.4 -> 7.2 m/s, settling into car-following at ~15 m gap
```

In-process and over gRPC give **the same score, distance and route** (85.6,
359.2 m, 58.1%); only latency changes, 0.014 ms to 0.934 ms p50.

`make test`: **122 passed**.

## Known issues

1. **Route completion is 58%, not 100%.** The ego correctly ends up following a
   vehicle doing 8 m/s, so it covers less ground in 40 s. The score charges
   8.4 points for it. Whether "followed a slow car safely" should be penalised
   as an incomplete route is a scoring question worth revisiting - spec section
   36 says it is.
2. **The lateral controller has no feedback on cross-track error**, only pure
   pursuit onto a lookahead point. Good enough on a highway; it will cut
   corners on tight turns.
3. **IDM is tuned by hand** and only against this one scenario.
4. **Jerk is still unfiltered** and still not part of the score.
