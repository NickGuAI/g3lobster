"""Shared control-plane domain types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlanStep:
    """A single step in a multi-agent plan."""

    id: str
    prompt: str
    agent_id: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """A plan with one or more ordered or dependency-linked steps."""

    steps: List[PlanStep]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegationRequest:
    """Structured delegation payload used by MCP/API bridges."""

    parent_agent_id: str
    prompt: str
    agent_name: Optional[str] = None
    wait: bool = True
    timeout_s: float = 300.0
    parent_session_id: str = "default"
    parent_task_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Public-facing task result projection."""

    task_id: str
    status: str
    result_md: Optional[str] = None
    error: Optional[str] = None
    agent_id: Optional[str] = None
    parent_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "result_md": self.result_md,
            "error": self.error,
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
        }
