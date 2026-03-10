"""Control-plane package exports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from g3lobster.control_plane.dispatcher import BackpressureError, Dispatcher
from g3lobster.control_plane.orchestrator import Orchestrator
from g3lobster.control_plane.registry import TaskRegistry
from g3lobster.control_plane.task_unit import TaskUnit, TaskUnitStatus


@dataclass
class ControlPlane:
    task_registry: TaskRegistry
    dispatcher: Dispatcher
    orchestrator: Orchestrator
    tmux_spawner: Optional[object] = None


__all__ = [
    "BackpressureError",
    "ControlPlane",
    "Dispatcher",
    "Orchestrator",
    "TaskRegistry",
    "TaskUnit",
    "TaskUnitStatus",
]
