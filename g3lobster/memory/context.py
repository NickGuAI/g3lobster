"""Prompt context builder from markdown memory + transcript history."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.procedures import Procedure


_STRUCTURE_PREAMBLE_TEMPLATE = """\
# G3Lobster Agent Environment
You are an agent running inside g3lobster. Key file locations for your reference:
- Agent data directory: {data_dir}
- Session transcripts:  {data_dir}/sessions/<session_id>.jsonl
- Agent memory:         {data_dir}/.memory/MEMORY.md
- Agent procedures:     {data_dir}/.memory/PROCEDURES.md
- Cron tasks:           {data_dir}/crons.json
- Global memory dir:    {global_data_dir}/.memory/
You can read these files to understand your own state. Cron tasks are JSON objects
with fields: id, agent_id, schedule (cron expr), instruction, enabled, last_run, next_run.
Slash-commands (/cron list, /cron add, /help) are handled directly by the bridge without
reaching you — you do not need to implement them yourself.\
"""


class ContextBuilder:
    """Builds request context from MEMORY.md + recent transcript entries."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        message_limit: int = 12,
        system_preamble: str = "",
        global_memory_manager: Optional[GlobalMemoryManager] = None,
        procedure_limit: int = 3,
        agent_list_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ):
        self.memory_manager = memory_manager
        self.message_limit = message_limit
        self.system_preamble = system_preamble.strip()
        self.global_memory_manager = global_memory_manager
        self.procedure_limit = max(1, int(procedure_limit))
        self.agent_list_provider = agent_list_provider

    def _structure_preamble(self) -> str:
        data_dir = str(self.memory_manager.data_dir)
        if self.global_memory_manager:
            global_data_dir = str(self.global_memory_manager.data_dir)
        else:
            import os
            global_data_dir = os.path.dirname(data_dir)
        return _STRUCTURE_PREAMBLE_TEMPLATE.format(
            data_dir=data_dir,
            global_data_dir=global_data_dir,
        )

    def build(self, session_id: str, prompt: str) -> str:
        memory_text = self.memory_manager.read_memory().strip()
        recent_entries = self.memory_manager.read_session_messages(session_id, limit=self.message_limit)
        compaction = self.memory_manager.read_latest_compaction(session_id) or {}

        user_memory = "(empty)"
        global_procedures: List[Procedure] = []
        if self.global_memory_manager:
            user_memory_text = self.global_memory_manager.read_user_memory().strip()
            user_memory = user_memory_text or "(empty)"
            global_procedures = self.global_memory_manager.procedures.list_procedures()

        matched = self.memory_manager.match_procedures(
            prompt,
            global_procedures=global_procedures,
            limit=self.procedure_limit,
        )

        history_lines: List[str] = []
        for entry in recent_entries:
            message = entry.get("message", {})
            role = message.get("role")
            content = str(message.get("content", "")).strip()
            if not role or not content:
                continue
            history_lines.append(f"{role}: {content}")

        parts = [self._structure_preamble(), ""]
        if self.system_preamble:
            parts.extend(
                [
                    "# Agent Persona",
                    self.system_preamble,
                    "",
                ]
            )

        delegation_section = self._format_available_agents()
        if delegation_section:
            parts.extend(
                [
                    "# Available Agents for Delegation",
                    "You can delegate tasks to other agents using the delegate_to_agent tool.",
                    delegation_section,
                    "",
                ]
            )

        parts.extend(
            [
                "# User Preferences",
                user_memory,
                "",
                "# Agent Memory",
                memory_text or "(empty)",
                "",
                "# Known Procedures",
                self._format_procedures(matched),
                "",
                "# Compaction Summary",
                str(compaction.get("summary", "")).strip() or "(none)",
                "",
                "# Recent Conversation",
                "\n".join(history_lines) if history_lines else "(none)",
                "",
                "# New User Prompt",
                prompt.strip(),
            ]
        )
        return "\n".join(parts).strip() + "\n"

    def _format_available_agents(self) -> str:
        """Format the list of available sibling agents for the preamble."""
        if not self.agent_list_provider:
            return ""
        try:
            agents = self.agent_list_provider()
        except Exception:
            return ""
        if not agents:
            return ""

        lines: List[str] = []
        for agent in agents:
            agent_id = agent.get("id", "unknown")
            name = agent.get("name", agent_id)
            emoji = agent.get("emoji", "")
            description = agent.get("description", "")
            entry = f"- {emoji} **{name}** (id: `{agent_id}`)"
            if description:
                entry += f": {description}"
            lines.append(entry)
        return "\n".join(lines)

    @staticmethod
    def _format_procedures(procedures: List[Procedure]) -> str:
        if not procedures:
            return "(none)"

        lines: List[str] = []
        for procedure in procedures:
            lines.append(f"## {procedure.title}")
            lines.append(f"Trigger: {procedure.trigger}")
            lines.append("Steps:")
            for index, step in enumerate(procedure.steps, start=1):
                lines.append(f"{index}. {step}")
            lines.append("")
        return "\n".join(lines).rstrip()
