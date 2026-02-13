"""Task domain types."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskPriority(int, Enum):
    LOW = 30
    NORMAL = 50
    HIGH = 70
    CRITICAL = 90

    @classmethod
    def from_value(cls, value: str | int | None) -> "TaskPriority":
        if value is None:
            return cls.NORMAL
        if isinstance(value, int):
            for item in cls:
                if item.value == value:
                    return item
            return cls.NORMAL
        normalized = str(value).strip().lower()
        mapping = {
            "low": cls.LOW,
            "normal": cls.NORMAL,
            "high": cls.HIGH,
            "critical": cls.CRITICAL,
        }
        return mapping.get(normalized, cls.NORMAL)


@dataclass
class TaskEvent:
    timestamp: float
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    prompt: str
    priority: TaskPriority = TaskPriority.NORMAL
    timeout_s: float = 120.0
    mcp_servers: List[str] = field(default_factory=list)
    session_id: str = "default"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    agent_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    events: List[TaskEvent] = field(default_factory=list)

    def add_event(self, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.events.append(TaskEvent(timestamp=time.time(), kind=kind, payload=payload or {}))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "priority": self.priority.name.lower(),
            "timeout_s": self.timeout_s,
            "mcp_servers": self.mcp_servers,
            "session_id": self.session_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
