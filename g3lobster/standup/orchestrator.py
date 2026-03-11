"""Standup conductor orchestrator — prompts team, collects responses, and generates summaries."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from g3lobster.standup.store import StandupConfig, StandupEntry, StandupStore

_BLOCKER_KEYWORDS = re.compile(
    r"block|stuck|waiting on|waiting for|need help|impediment|can't proceed|depends on",
    re.IGNORECASE,
)

_ACTION_KEYWORDS = re.compile(
    r"\bwill\b|going to|plan to|need to|working on|today i",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class StandupOrchestrator:
    """Orchestrates standup prompting, response collection, and summary generation."""

    def __init__(self, store: StandupStore, registry, chat_bridge=None):
        self._store = store
        self._registry = registry
        self._chat_bridge = chat_bridge
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Prompt team members
    # ------------------------------------------------------------------

    async def prompt_team(self, agent_id: str) -> None:
        """Send standup prompts to each team member via the chat bridge."""
        if self._chat_bridge is None:
            self._logger.warning(
                "No chat_bridge configured — cannot prompt team for agent %s",
                agent_id,
            )
            return

        config = self._store.get_config(agent_id)
        if config is None:
            self._logger.warning("No standup config found for agent %s", agent_id)
            return

        runtime = self._registry.get_agent(agent_id)
        if runtime is None:
            self._logger.warning("Agent %s not found in registry", agent_id)
            return

        persona = runtime.persona
        for member in config.team_members:
            display = member.get("display_name", member.get("user_id", "")) if isinstance(member, dict) else member.display_name
            message = (
                f"{persona.emoji} {persona.name}: "
                f"Hey {display}! Time for standup. "
                f"{config.prompt_template}"
            )
            await self._chat_bridge.send_message(message)

    # ------------------------------------------------------------------
    # Collect a response
    # ------------------------------------------------------------------

    def collect_response(
        self,
        agent_id: str,
        user_id: str,
        display_name: str,
        text: str,
    ) -> Optional[StandupEntry]:
        """Parse a standup response, store it, and return the created entry."""
        from g3lobster.standup.store import StandupEntry

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        blockers = self._extract_blockers(text)

        entry = StandupEntry(
            user_id=user_id,
            display_name=display_name,
            date=today,
            response=text,
            blockers=blockers,
        )
        self._store.add_entry(agent_id, entry)
        return entry

    # ------------------------------------------------------------------
    # Generate summary
    # ------------------------------------------------------------------

    async def generate_summary(self, agent_id: str) -> Optional[str]:
        """Build a markdown standup summary for today and optionally post it."""
        config = self._store.get_config(agent_id)
        if config is None:
            self._logger.warning("No standup config found for agent %s", agent_id)
            return None

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        entries = self._store.get_entries(agent_id, today)

        # --- Build summary sections ---
        sections: List[str] = []
        sections.append(f"# Standup Summary — {today}")

        # Updates
        if entries:
            lines = ["## Updates", ""]
            for entry in entries:
                lines.append(f"**{entry.display_name}**")
                lines.append(entry.response)
                lines.append("")
            sections.append("\n".join(lines))
        else:
            sections.append("## Updates\n\n_No updates received today._")

        # Blockers
        all_blockers: List[str] = []
        for entry in entries:
            for blocker in entry.blockers:
                all_blockers.append(f"- **{entry.display_name}**: {blocker}")
        if all_blockers:
            sections.append("## Blockers\n\n" + "\n".join(all_blockers))

        # Missing updates
        responded_ids = {entry.user_id for entry in entries}
        missing = []
        for m in config.team_members:
            uid = m.get("user_id", "") if isinstance(m, dict) else m.user_id
            name = m.get("display_name", uid) if isinstance(m, dict) else m.display_name
            if uid not in responded_ids:
                missing.append(name)
        if missing:
            missing_lines = "\n".join(f"- {name}" for name in missing)
            sections.append(f"## Missing Updates\n\n{missing_lines}")

        # Action items
        all_actions: List[str] = []
        for entry in entries:
            for action in self._extract_action_items(entry.response):
                all_actions.append(f"- **{entry.display_name}**: {action}")
        if all_actions:
            sections.append("## Action Items\n\n" + "\n".join(all_actions))

        summary = "\n\n".join(sections)

        # Post to configured space if chat bridge is available
        if self._chat_bridge is not None and config.summary_space_id:
            await self._chat_bridge.send_message(summary)

        return summary

    # ------------------------------------------------------------------
    # Membership check
    # ------------------------------------------------------------------

    def is_standup_participant(self, agent_id: str, user_id: str) -> bool:
        """Return True if *user_id* is in the agent's standup team members."""
        config = self._store.get_config(agent_id)
        if config is None:
            return False
        for m in config.team_members:
            uid = m.get("user_id", "") if isinstance(m, dict) else m.user_id
            if uid == user_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_blockers(self, text: str) -> List[str]:
        """Return sentences containing blocker-related keywords."""
        sentences = _SENTENCE_SPLIT.split(text)
        return [s.strip() for s in sentences if _BLOCKER_KEYWORDS.search(s)]

    def _extract_action_items(self, text: str) -> List[str]:
        """Return sentences containing action-item keywords."""
        sentences = _SENTENCE_SPLIT.split(text)
        return [s.strip() for s in sentences if _ACTION_KEYWORDS.search(s)]
