# Phase 11 - Learned driving model

Goal: prove the platform takes a real neural driving policy, and see how one
built from this data actually behaves.

**Status: complete. The model drives closed-loop; it does not complete the
scenario.** That is reported as measured rather than tuned until it looks
good, and the reason is diagnosed below.

## What was built

| Path | Purpose |
|---|---|
| `scripts/collect_dataset.py` | Drive with the PID expert, record camera + labels |
| `models/il/dataset.py` | Episode artefacts -> training samples |
| `models/il/model.py` | ResNet-18 + speed -> control + trajectory |
| `models/il/train.py` | Training, episode-level split |
| `models/il/policy.py` | The trained model as a `DrivingPolicy` |
| `simulator/control.py` | `TrajectoryController` - waypoints -> pedals |
| `simulator/types.py` | `TrajectoryAction` (spec section 13) |
| `model_gateway/protocol/driving.proto` | `TRAJECTORY_POLICY` over gRPC |
| `tests/test_trajectory.py` | 14 tests on the trajectory path |

This is a **driving policy**, not object detection: camera and speed in,
control and a two-second path out. It declares `("rgb_front", "speed")` and
nothing else - unlike the PID expert it learned from, it gets no route and no
lead-vehicle ground truth, and has to read the road out of the image.

## Dataset

60 episodes driven by the PID expert, sweeping 12 spawn points and 5 weather
presets, with the cut-in distance, NPC speed and post-cut-in speed randomised
per episode. Episodes the expert drove badly are discarded - imitating a crash
teaches crashing.

```
23,639 samples in 35.8 min -> dataset/town04_pid   (736 MB)
```

Training uses 22,626 of them (samples too close to the end have no full
two-second horizon). **Whole episodes are held out**, never random frames:
consecutive frames are nearly identical, so a random split leaks validation
into training and the loss looks far better than the model is.

## Training

```
device: cuda (NVIDIA RTX PRO 6000 Blackwell Server Edition)
22626 samples: 19199 train, 3427 validation
held-out episodes: DS-0051 ... DS-0059

epoch  1/15  train 0.6376 (ctrl 0.0746, wp 1.4075)  val 0.2781 (ctrl 0.0470, wp 0.5776, steer MAE 0.0152)
epoch  5/15  train 0.1916 (ctrl 0.0297, wp 0.4047)  val 0.1324 (ctrl 0.0278, wp 0.2615, steer MAE 0.0092)
epoch 10/15  train 0.1389 (ctrl 0.0178, wp 0.3028)  val 0.1578 (ctrl 0.0231, wp 0.3367, steer MAE 0.0045)
epoch 15/15  train 0.1193 (ctrl 0.0135, wp 0.2646)  val 0.1778 (ctrl 0.0214, wp 0.3909, steer MAE 0.0054)

best validation loss 0.1324 after 13.9 min
```

Best is epoch 5; the run overfits after that (training loss keeps falling while
validation rises), and the kept checkpoint is the best one.

## The result, and why the good-looking metric was worthless

Closed loop on Highway Cut-In, same scenario and seed for every model:

| model | result | score | distance | route | ticks survived |
|---|---|---|---|---|---|
| DummyAgent | FAIL | 0.0 | 253.6 m | 40.6% | 672 |
| **cnn_il (control head)** | FAIL | 0.0 | **76.1 m** | 12.3% | 158 |
| **cnn_il (trajectory head)** | FAIL | 0.0 | **260.7 m** | 42.3% | 470 |
| PIDAgent (the expert) | PASS | 82.7 | 360.8 m | 58.4% | 800 |

### The control head collapsed to a constant

Validation steer MAE was **0.005**, which looks excellent. The model was
useless. Driving it produced:

```
steer: mean 0.010   min 0.000   max 0.011
```

A constant. Steering labels on a near-straight highway have a standard
deviation of 0.009, so L1 loss is minimised by predicting their mean - and the
metric rewards exactly that. The car drifted steadily right (x: 405 -> 416) and
hit the barrier at 7.9 s.

This is the phase's real lesson: **a low error on a low-variance label measures
nothing**. Speed control, whose labels do vary, was learned fine - the car
accelerated smoothly to 14 m/s on sensible throttle.

### The trajectory head does not degenerate the same way

Waypoints carry real spatial variance, so the same network trained in the same
run produces a usable path. Driving from it through the trajectory controller:

```
steer: mean -0.0081  min -0.0458  max 0.0086  std 0.0133
```

Actual steering, responding to the road. **3.4x further than the control head**
(260.7 m vs 76.1 m) and past the dummy's distance, from a model that sees only
pixels and speed.

That is also the point of having built `TRAJECTORY_POLICY`: it is the output
shape TCP, Transfuser, InterFuser and LAV all use, and this phase demonstrates
the platform executing it.

### It still does not finish

Twelve lane invasions and a collision at 23.5 s. This is covariate shift, the
standard failure of behaviour cloning: the model only ever saw states the
expert visited, so a small deviation puts it somewhere it has no training
signal for, and the error compounds. Fixing it properly means DAgger - running
the learner, having the expert label the states it actually reaches, and
retraining - not more of the same data.

## Other things worth recording

**The first inference blew the deadline.** Tick 0 recorded a timeout and an
invalid action: the first CUDA inference includes kernel autotuning and took
over 500 ms. The policy now warms up inside `reset()`, where the budget is ten
times longer and nothing is driving. Timeouts went from 1 to 0.

**Real inference latency, at last.** p50 13.5 ms, p95 21.0 ms - a ResNet-18
forward pass on the GPU across a gRPC boundary. Every previous model reported
microseconds because it was arithmetic.

## Known issues

1. **The model does not complete the scenario.** Covariate shift; DAgger is the
   fix, and it is not implemented.
2. **The control head is unusable** on this dataset and is kept only so the
   collapse is reproducible. `--mode trajectory` is the default.
3. **Weighting steering higher in the loss** was not tried, and might partly
   rescue the control head. Neither was rebalancing the dataset towards curves.
4. **One town, one scenario family.** Twelve spawn points on Town04 is variety
   within a highway, not across driving situations.
5. **The checkpoint is not committed** - 45 MB of weights do not belong in git.
   `models/il/train.py` regenerates it from the recorded dataset.
6. **The dataset is not committed either** (736 MB); `scripts/collect_dataset.py`
   regenerates it in about 36 minutes.
