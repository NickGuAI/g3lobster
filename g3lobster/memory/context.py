"""Prompt context builder from markdown memory + transcript history."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from g3lobster.memory.global_memory import GlobalMemoryManager, _parse_frontmatter
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.procedures import Procedure

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

_KNOWLEDGE_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "my", "of", "on", "or", "please",
    "the", "this", "to", "we", "with", "you",
}


def _tokenize_for_matching(text: str) -> set[str]:
    tokens = set(_TOKEN_PATTERN.findall(text.lower()))
    return tokens - _KNOWLEDGE_STOP_WORDS


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
        self.knowledge_limit = 3
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

        knowledge_section = self._build_knowledge_section(prompt)

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
            ]
        )

        if knowledge_section:
            parts.extend(
                [
                    "# Cross-Agent Knowledge",
                    knowledge_section,
                    "",
                ]
            )

        parts.extend(
            [
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

    def _build_knowledge_section(self, prompt: str) -> str:
        """Match and format relevant cross-agent knowledge entries."""
        if not self.global_memory_manager:
            return ""
        all_knowledge = self.global_memory_manager.read_all_knowledge()
        if not all_knowledge:
            return ""

        query_tokens = _tokenize_for_matching(prompt)
        if not query_tokens:
            return ""

        scored: list[tuple[float, str, str]] = []
        for rel_path, content in all_knowledge.items():
            meta = _parse_frontmatter(content)
            # Build match text from title, topic, and body
            body = content.split("---", 2)[-1].strip() if content.startswith("---") else content
            match_text = f"{meta.get('topic', '')} {body}"
            entry_tokens = _tokenize_for_matching(match_text)
            if not entry_tokens:
                continue
            overlap = len(query_tokens & entry_tokens) / len(query_tokens | entry_tokens)
            if overlap >= 0.1:
                scored.append((overlap, rel_path, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: self.knowledge_limit]
        if not top:
            return ""

        lines: list[str] = []
        for _score, _path, content in top:
            meta = _parse_frontmatter(content)
            body = content.split("---", 2)[-1].strip() if content.startswith("---") else content
            source = meta.get("source", "unknown")
            topic = meta.get("topic", "general")
            lines.append(f"_Source: {source}, Topic: {topic}_")
            lines.append(body)
            lines.append("")
        return "\n".join(lines).rstrip()

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
