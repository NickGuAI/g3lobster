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
