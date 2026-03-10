"""Task unit state machine for control-plane orchestration."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class TaskUnitStatus(str, Enum):
    SUBMITTED = "submitted"
    QUEUED = "queued"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = {
    TaskUnitStatus.COMPLETED,
    TaskUnitStatus.FAILED,
    TaskUnitStatus.CANCELLED,
}

_VALID_TRANSITIONS = {
    TaskUnitStatus.SUBMITTED: {TaskUnitStatus.QUEUED, TaskUnitStatus.CANCELLED},
    TaskUnitStatus.QUEUED: {TaskUnitStatus.WORKING, TaskUnitStatus.CANCELLED},
    TaskUnitStatus.WORKING: {
        TaskUnitStatus.COMPLETED,
        TaskUnitStatus.FAILED,
        TaskUnitStatus.CANCELLED,
    },
    TaskUnitStatus.COMPLETED: set(),
    TaskUnitStatus.FAILED: set(),
    TaskUnitStatus.CANCELLED: set(),
}


@dataclass
class TaskUnit:
    """Represents a single unit of control-plane work."""

    prompt: str
    source: str = "human"
    parent_id: Optional[str] = None
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskUnitStatus = TaskUnitStatus.SUBMITTED
    result_md: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def can_transition(self, next_status: TaskUnitStatus) -> bool:
        if self.status == next_status:
            return True
        return next_status in _VALID_TRANSITIONS[self.status]

    def transition(
        self,
        next_status: TaskUnitStatus,
        *,
        agent_id: Optional[str] = None,
        result_md: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        if not self.can_transition(next_status):
            raise ValueError(f"Invalid task transition: {self.status.value} -> {next_status.value}")

        now = time.time()
        self.status = next_status

        if agent_id is not None:
            self.agent_id = agent_id

        if next_status == TaskUnitStatus.WORKING and self.started_at is None:
            self.started_at = now

        if next_status in _TERMINAL_STATUSES:
            if self.completed_at is None:
                self.completed_at = now
            if result_md is not None:
                self.result_md = result_md
            if error is not None:
                self.error = error

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "agent_id": self.agent_id,
            "source": self.source,
            "status": self.status.value,
            "prompt": self.prompt,
            "result_md": self.result_md,
            "error": self.error,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
