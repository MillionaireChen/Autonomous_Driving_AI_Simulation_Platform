# Phase 8 - Bird-Eye View

Goal: see the geometry of the manoeuvre, not just the camera.

**Status: complete.** Built alongside the dashboard in Phase 7 and shipped in
the same commit, since it is one canvas inside that page.

![bird-eye view](images/dashboard-result.png)

## What was built

`frontend/components/BirdEyeView.tsx` - a canvas fed by the telemetry already
being streamed. No new backend work beyond adding the scenario vehicle's
absolute pose to each tick.

## Design decisions

### Drawn from telemetry, not rendered by CARLA

Spec section 55 is explicit and it is the right call: a second CARLA camera for
a top-down view would cost GPU time, bandwidth and encode latency to express
what two rectangles and a polyline already say. The BEV consumes fields the
socket carries anyway.

The only addition was `npc_x`, `npc_y`, `npc_yaw` on each tick. The existing
`npc_lateral_m` is measured relative to the ego's heading, which is useful for
a trigger but useless for drawing - a car 100 m ahead on a curve reads as tens
of metres off-axis without having changed lanes.

### Ego-centred, ego-up

The world rotates around the car rather than the car moving across a map. That
is what a driver's view looks like, and it makes a lane change read as lateral
motion instead of as a rotation of the whole scene.

The transform is a rotation by `-yaw` into the ego frame, then forward maps to
screen-up and right to screen-right:

```
fx =  dx·cos(yaw) + dy·sin(yaw)     // forward
fy = -dx·sin(yaw) + dy·cos(yaw)     // right
screen = (w/2 + fy·scale, h/2 - fx·scale)
```

### What is drawn

- Range rings every 20 m, so distances are readable without a scale bar.
- The ego's trail, last 400 ticks (20 s), which makes drift and lane changes
  visible after the fact.
- Ego in blue, scenario vehicle in amber, each a to-scale 4.6 x 2.0 m rectangle
  with a white nose marker so heading is unambiguous.

Cars are drawn at true size: at the initial 70 m half-view they came out
14 x 6 px, too small to read. The view is now 42 m, which fits the whole cut-in
manoeuvre while keeping the vehicles legible.

## Verification

From the captured screenshot of a real run: ego and scenario vehicle both
drawn in the correct relative position, the amber NPC ahead and to the left
matching `gap 2.8 m` in the side panel, the ego's trail running back down the
lane it came from, and range rings at 20 and 40 m.

## Known issues

1. **No lane geometry.** The road itself is not drawn - there is no map data on
   the wire, only vehicle poses. Lane centrelines would need the backend to
   send waypoints, which is worth doing when a planner starts emitting
   trajectories to overlay.
2. **Only the scenario vehicle is drawn.** Background traffic is not in the
   telemetry, so a busy scenario would show an empty road around the ego.
3. **No predicted trajectory yet.** Spec section 56 wants a planner's output
   overlaid; there is no trajectory-producing model until the trajectory
   policy lands.
