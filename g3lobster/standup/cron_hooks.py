from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from g3lobster.standup.orchestrator import StandupOrchestrator

logger = logging.getLogger(__name__)


async def handle_standup_cron(instruction: str, agent_id: str, orchestrator: Optional["StandupOrchestrator"]) -> bool:
    """Intercept standup cron instructions before they reach the agent.

    Returns True if the instruction was handled, False otherwise.
    """
    if not orchestrator:
        return False

    if instruction == "__standup_prompt__":
        logger.info("Firing standup prompt for agent %s", agent_id)
        await orchestrator.prompt_team(agent_id)
        return True

    if instruction == "__standup_summary__":
        logger.info("Firing standup summary for agent %s", agent_id)
        await orchestrator.generate_summary(agent_id)
        return True

    return False
