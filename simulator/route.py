"""Route generation and progress tracking.

Route completion needs a route. There is no global mission planner yet, so a
route is built by following the ego's starting lane forward for a configured
distance. That is enough to answer "how far along did it get", which is what
the score needs (spec section 34).

Progress is measured by arc length along the polyline, not straight-line
distance from the start, so a curving road is not scored as if it were short.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import carla


@dataclass
class Route:
    """A polyline of (x, y) points with cumulative arc length."""

    points: list[tuple[float, float]] = field(default_factory=list)
    cumulative: list[float] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return self.cumulative[-1] if self.cumulative else 0.0

    @classmethod
    def from_waypoints(cls, waypoints: list[carla.Waypoint]) -> "Route":
        points = [(wp.transform.location.x, wp.transform.location.y) for wp in waypoints]
        cumulative = [0.0]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            cumulative.append(cumulative[-1] + math.dist((x0, y0), (x1, y1)))
        return cls(points=points, cumulative=cumulative)

    def progress_m(self, x: float, y: float) -> float:
        """Arc length of the closest point on the route to (x, y).

        Monotonic in practice but not enforced: a vehicle that turns around
        will report decreasing progress, which is the honest answer.
        """
        if not self.points:
            return 0.0
        best_index, best_distance = 0, float("inf")
        for index, (px, py) in enumerate(self.points):
            distance = (px - x) ** 2 + (py - y) ** 2
            if distance < best_distance:
                best_index, best_distance = index, distance
        return self.cumulative[best_index]

    def upcoming(self, x: float, y: float, count: int = 20,
                 stride: int = 2) -> list[tuple[float, float]]:
        """The next `count` route points ahead of (x, y), nearest first.

        Strided so the horizon covers useful distance without shipping every
        point: at a 2 m step, stride 2 and count 20 is 80 m of road.
        """
        if not self.points:
            return []
        best_index, best_distance = 0, float("inf")
        for index, (px, py) in enumerate(self.points):
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_distance:
                best_index, best_distance = index, d
        return self.points[best_index + 1: best_index + 1 + count * stride: stride]

    def completion_percent(self, x: float, y: float) -> float:
        if self.length_m <= 0.0:
            return 0.0
        return min(100.0, max(0.0, 100.0 * self.progress_m(x, y) / self.length_m))


def build_route(
    world_map: carla.Map,
    start: carla.Location,
    length_m: float,
    step_m: float = 2.0,
) -> Route:
    """Follow the lane forward from `start` for `length_m`.

    At a fork the first successor is taken, which keeps the route
    deterministic for a given map and start point (spec section 74).
    """
    waypoint = world_map.get_waypoint(
        start, project_to_road=True, lane_type=carla.LaneType.Driving
    )
    if waypoint is None:
        return Route()

    waypoints = [waypoint]
    travelled = 0.0
    while travelled < length_m:
        successors = waypoint.next(step_m)
        if not successors:
            break  # ran out of road; the route is as long as the road allows
        waypoint = successors[0]
        waypoints.append(waypoint)
        travelled += step_m
    return Route.from_waypoints(waypoints)
