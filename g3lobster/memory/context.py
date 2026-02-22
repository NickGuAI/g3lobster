"""Prompt context builder from markdown memory + transcript history."""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.procedures import Procedure


class ContextBuilder:
    """Builds request context from MEMORY.md + recent transcript entries."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        message_limit: int = 12,
        system_preamble: str = "",
        global_memory_manager: Optional[GlobalMemoryManager] = None,
        procedure_limit: int = 3,
        agent_id: Optional[str] = None,
        delegation_agents_provider: Optional[Callable[[], Sequence[Tuple[str, str]]]] = None,
    ):
        self.memory_manager = memory_manager
        self.message_limit = message_limit
        self.system_preamble = system_preamble.strip()
        self.global_memory_manager = global_memory_manager
        self.procedure_limit = max(1, int(procedure_limit))
        self.agent_id = str(agent_id).strip() if agent_id else None
        self.delegation_agents_provider = delegation_agents_provider

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

        parts = []
        persona_preamble = self.system_preamble
        delegation_section = self._format_delegation_agents()
        if delegation_section:
            persona_preamble = f"{persona_preamble}\n\n{delegation_section}".strip()

        if persona_preamble:
            parts.extend(
                [
                    "# Agent Persona",
                    persona_preamble,
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

    def _format_delegation_agents(self) -> str:
        if not self.delegation_agents_provider:
            return ""

        raw_items = self.delegation_agents_provider() or []
        peers: List[Tuple[str, str]] = []
        for item in raw_items:
            if not item or len(item) < 1:
                continue
            agent_id = str(item[0]).strip()
            if not agent_id:
                continue
            if self.agent_id and agent_id == self.agent_id:
                continue
            description = str(item[1]).strip() if len(item) > 1 else ""
            peers.append((agent_id, description or "No description provided."))

        lines = [
            "## Available Agents for Delegation",
            "You can delegate tasks to other agents using the delegate_to_agent tool.",
        ]
        if not peers:
            lines.append("Available agents: (none)")
            return "\n".join(lines)

        lines.append("Available agents:")
        for agent_id, description in sorted(peers):
            lines.append(f"- {agent_id}: {description}")
        return "\n".join(lines)


def summarize_agent_soul(soul_text: str, max_length: int = 140) -> str:
    """Extract a short one-line summary from SOUL.md-like text."""
    for raw_line in str(soul_text or "").splitlines():
        candidate = raw_line.strip()
        if not candidate:
            continue
        candidate = candidate.lstrip("#").strip()
        if candidate:
            return candidate[:max_length]
    return "No description provided."
