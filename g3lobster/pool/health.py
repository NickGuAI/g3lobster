"""Health checks for pool agents."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List

from g3lobster.pool.types import AgentState


@dataclass
class HealthIssue:
    agent_id: str
    issue: str


class HealthInspector:
    """Detects dead and stuck agents from runtime metadata."""

    def inspect(self, agents: List[object], stuck_timeout_s: int) -> List[HealthIssue]:
        now = time.time()
        issues: List[HealthIssue] = []

        for agent in agents:
            state = getattr(agent, "state", None)
            agent_id = str(getattr(agent, "id", "unknown"))

            if state == AgentState.BUSY:
                busy_since = getattr(agent, "busy_since", None)
                if busy_since and (now - busy_since) > stuck_timeout_s:
                    issues.append(HealthIssue(agent_id=agent_id, issue="stuck"))
                continue

            is_alive = getattr(agent, "is_alive", None)
            if callable(is_alive) and not is_alive() and state not in {AgentState.STOPPED, AgentState.STARTING}:
                issues.append(HealthIssue(agent_id=agent_id, issue="dead"))

        return issues

    def inspect_orphaned_tasks(self, tasks: Iterable[object], active_agent_ids: Iterable[str]) -> List[str]:
        """Detect queued/working tasks assigned to agents that are no longer active."""
        active = set(active_agent_ids)
        orphaned_ids: List[str] = []

        for task in tasks:
            raw_status = getattr(task, "status", "")
            status = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
            if status not in {"queued", "working"}:
                continue

            agent_id = getattr(task, "agent_id", None)
            if not agent_id or agent_id in active:
                continue

            task_id = str(getattr(task, "id", "")).strip()
            if task_id:
                orphaned_ids.append(task_id)

        return orphaned_ids
