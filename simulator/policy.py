"""The interface every driving model implements (spec section 10).

The simulator knows nothing about what is behind this interface: a rule-based
controller, a CNN, a planner, or a gRPC stub forwarding to another machine.
It may never import a specific network (spec section 84.10).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from simulator.types import Observation, VehicleControlAction


class DrivingPolicy(ABC):
    """Observation in, action out."""

    #: Human-readable name recorded in episode results.
    name: str = "unnamed"

    #: Sensors this policy needs. The worker uses it to check that the
    #: configured sensor suite can actually feed the model.
    required_sensors: tuple[str, ...] = ()

    @abstractmethod
    def reset(self, config: dict[str, Any]) -> None:
        """Prepare for a new episode."""

    @abstractmethod
    def infer(self, observation: Observation) -> VehicleControlAction:
        """Return the control to apply for this observation."""

    def close(self) -> None:
        """Release any resources. Safe to call more than once."""
        return None
