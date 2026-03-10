"""Health checks for pool agents."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from g3lobster.pool.types import AgentState


@dataclass
class HealthIssue:
    agent_id: str
    issue: str


class HealthInspector:
    """Detects dead and stuck agents from runtime metadata."""

    @staticmethod
    def _task_has_timeout(agent: object) -> bool:
        task = getattr(agent, "current_task", None)
        if task is None:
            return True

        timeout_s = getattr(task, "timeout_s", None)
        if timeout_s is None:
            return False

        try:
            return float(timeout_s) > 0
        except (TypeError, ValueError):
            return True

    def inspect(self, agents: List[object], stuck_timeout_s: int) -> List[HealthIssue]:
        now = time.time()
        issues: List[HealthIssue] = []
        stuck_enabled = stuck_timeout_s > 0

        for agent in agents:
            state = getattr(agent, "state", None)
            agent_id = str(getattr(agent, "id", "unknown"))

            if state == AgentState.BUSY:
                busy_since = getattr(agent, "busy_since", None)
                if (
                    stuck_enabled
                    and busy_since
                    and self._task_has_timeout(agent)
                    and (now - busy_since) > stuck_timeout_s
                ):
                    issues.append(HealthIssue(agent_id=agent_id, issue="stuck"))
                continue

            is_alive = getattr(agent, "is_alive", None)
            if callable(is_alive) and not is_alive() and state not in {AgentState.STOPPED, AgentState.STARTING}:
                issues.append(HealthIssue(agent_id=agent_id, issue="dead"))

        return issues
