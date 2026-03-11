"""Task domain types."""

from __future__ import annotations

import copy
import time
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional


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
    timeout_s: Optional[float] = 120.0
    mcp_servers: List[str] = field(default_factory=list)
    session_id: str = "default"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    agent_id: Optional[str] = None
    space_id: Optional[str] = None
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
            "space_id": self.space_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "events": [
                {
                    "timestamp": event.timestamp,
                    "kind": event.kind,
                    "payload": dict(event.payload),
                }
                for event in self.events
            ],
        }


class TaskStore:
    """In-memory ring buffer of recent tasks per agent."""

    def __init__(self, max_tasks_per_agent: int = 100):
        self.max_tasks_per_agent = max(1, int(max_tasks_per_agent))
        self._lock = threading.Lock()
        self._tasks_by_agent: Dict[str, Deque[Task]] = {}

    def add(self, task: Task) -> None:
        agent_id = str(task.agent_id or "").strip()
        if not agent_id:
            return

        task_copy: Task = copy.deepcopy(task)
        with self._lock:
            bucket = self._tasks_by_agent.setdefault(agent_id, deque())

            # Replace existing task entry if present, otherwise append.
            for index, existing in enumerate(bucket):
                if existing.id == task_copy.id:
                    bucket[index] = task_copy
                    break
            else:
                bucket.append(task_copy)

            while len(bucket) > self.max_tasks_per_agent:
                bucket.popleft()

    def get(self, agent_id: str, task_id: str) -> Optional[Task]:
        key = str(agent_id or "").strip()
        if not key:
            return None

        with self._lock:
            bucket = self._tasks_by_agent.get(key, deque())
            for task in bucket:
                if task.id == task_id:
                    return copy.deepcopy(task)
        return None

    def list(self, agent_id: str, limit: Optional[int] = None) -> List[Task]:
        key = str(agent_id or "").strip()
        if not key:
            return []

        with self._lock:
            bucket = list(self._tasks_by_agent.get(key, deque()))

        items = [copy.deepcopy(task) for task in reversed(bucket)]
        if limit is None:
            return items
        return items[: max(0, int(limit))]
