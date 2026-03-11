"""Prompt context builder from markdown memory + transcript history."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from g3lobster.memory.global_memory import GlobalMemoryManager, _parse_frontmatter
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.procedures import Procedure

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_KNOWLEDGE_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "my", "of", "on", "or", "please",
    "the", "this", "to", "we", "with", "you",
}


def _tokenize_for_matching(text: str) -> set[str]:
    """Tokenize text for knowledge matching, stripping stop words."""
    tokens = set(_TOKEN_RE.findall(text.lower()))
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


def _estimate_tokens(text: str) -> int:
    """Approximate token count using len(text) // 4."""
    return len(text) // 4


@dataclass
class ContextLayer:
    """A single layer of context with a name, priority, and content."""

    name: str
    priority: int
    content: str
    required: bool = False

    @property
    def tokens(self) -> int:
        return _estimate_tokens(self.content)


@dataclass
class BuildInfo:
    """Debug info from the last build() call."""

    included: List[Dict[str, Any]] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    budget: int = 0


# Display order for assembling the final prompt (layer name -> position).
_DISPLAY_ORDER = [
    "preamble",
    "persona",
    "available_agents",
    "user_prefs",
    "cross_agent_knowledge",
    "memory",
    "recollection",
    "procedures",
    "compaction",
    "messages",
    "prompt",
]


class ContextBuilder:
    """Builds request context from MEMORY.md + recent transcript entries.

    Supports priority-based layer dropping when the assembled context exceeds
    a configurable token budget.
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        message_limit: int = 12,
        system_preamble: str = "",
        global_memory_manager: Optional[GlobalMemoryManager] = None,
        procedure_limit: int = 3,
        knowledge_limit: int = 3,
        agent_list_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        token_budget: int = 1_000_000,
        debug: bool = False,
    ):
        self.memory_manager = memory_manager
        self.message_limit = message_limit
        self.system_preamble = system_preamble.strip()
        self.global_memory_manager = global_memory_manager
        self.procedure_limit = max(1, int(procedure_limit))
        self.knowledge_limit = max(1, int(knowledge_limit))
        self.agent_list_provider = agent_list_provider
        self.token_budget = token_budget
        self.debug = debug
        self.last_build_info: Optional[BuildInfo] = None

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

    def _match_knowledge(self, prompt: str) -> List[Dict[str, Any]]:
        """Return top-N relevant knowledge entries matched against *prompt*.

        Uses Jaccard token overlap (same approach as ``ProcedureStore.match_query``)
        with a 0.1 threshold to keep only entries with at least some relevance.
        """
        if not self.global_memory_manager:
            return []
        entries = self.global_memory_manager.read_all_knowledge_with_metadata()
        if not entries:
            return []

        query_tokens = _tokenize_for_matching(prompt)
        if not query_tokens:
            return entries[: self.knowledge_limit]

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for entry in entries:
            # Match against body content + topic + key
            match_text = f"{entry['key']} {entry.get('topic', '')} {entry['content']}"
            entry_tokens = _tokenize_for_matching(match_text)
            if not entry_tokens:
                continue
            overlap = len(query_tokens & entry_tokens) / len(query_tokens | entry_tokens)
            if overlap >= 0.1:
                scored.append((overlap, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[: self.knowledge_limit]]

    def _format_cross_agent_knowledge(self, entries: List[Dict[str, Any]]) -> str:
        """Format matched knowledge entries for prompt injection."""
        if not entries:
            return ""
        lines: List[str] = []
        for entry in entries:
            title = entry.get("key", "unknown")
            source = entry.get("source", "unknown")
            topic = entry.get("topic", "")
            content = entry.get("content", "")
            header = f"## {title}"
            meta = f"_Source: {source}"
            if topic:
                meta += f", Topic: {topic}"
            meta += "_"
            lines.append(f"{header}\n{meta}\n{content}")
        return "\n\n".join(lines)

    def _recollection_layer(self) -> str:
        """Return recollection content from the association graph.

        This is a no-op stub until issue #63 (salience-classified journal /
        association graph) is merged.  Once #63 lands, this method should query
        the recollection system for contextually relevant memories.
        """
        return ""

    def build(self, session_id: str, prompt: str) -> str:
        # --- gather raw content for each layer ---
        memory_text = self.memory_manager.read_memory().strip()
        recent_entries = self.memory_manager.read_session_messages(
            session_id, limit=self.message_limit
        )
        compaction = self.memory_manager.read_latest_compaction(session_id) or {}

        user_memory = "(empty)"
        global_procedures: List[Procedure] = []
        if self.global_memory_manager:
            user_memory_text = self.global_memory_manager.read_user_memory().strip()
            user_memory = user_memory_text or "(empty)"
            global_procedures = self.global_memory_manager.procedures.list_procedures()

        # Knowledge injection is now handled by the cross-agent knowledge layer
        # which provides relevance-filtered results.

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

        # --- build delegation section ---
        delegation_section = self._format_available_agents()
        agents_content = ""
        if delegation_section:
            agents_content = (
                "# Available Agents for Delegation\n"
                "You can delegate tasks to other agents using the delegate_to_agent tool.\n"
                + delegation_section
            )

        # --- build persona section ---
        persona_content = ""
        if self.system_preamble:
            persona_content = "# Agent Persona\n" + self.system_preamble

        # --- cross-agent knowledge (relevance-filtered) ---
        matched_knowledge = self._match_knowledge(prompt)
        cross_agent_knowledge_content = ""
        if matched_knowledge:
            cross_agent_knowledge_content = (
                "# Cross-Agent Knowledge\n"
                + self._format_cross_agent_knowledge(matched_knowledge)
            )

        # --- recollection stub ---
        recollection_content = self._recollection_layer()

        # --- construct layers ---
        layers: List[ContextLayer] = [
            ContextLayer(
                name="preamble",
                priority=0,
                content=self._structure_preamble(),
                required=True,
            ),
            ContextLayer(
                name="persona",
                priority=1,
                content=persona_content,
                required=True,
            ),
            ContextLayer(
                name="prompt",
                priority=2,
                content="# New User Prompt\n" + prompt.strip(),
                required=True,
            ),
            ContextLayer(
                name="messages",
                priority=3,
                content=(
                    "# Recent Conversation\n"
                    + ("\n".join(history_lines) if history_lines else "(none)")
                ),
            ),
            ContextLayer(
                name="memory",
                priority=4,
                content="# Agent Memory\n" + (memory_text or "(empty)"),
            ),
            ContextLayer(
                name="user_prefs",
                priority=5,
                content="# User Preferences\n" + user_memory,
            ),
            ContextLayer(
                name="available_agents",
                priority=7,
                content=agents_content,
            ),
            ContextLayer(
                name="recollection",
                priority=8,
                content=recollection_content,
            ),
            ContextLayer(
                name="cross_agent_knowledge",
                priority=8,
                content=cross_agent_knowledge_content,
            ),
            ContextLayer(
                name="procedures",
                priority=9,
                content="# Known Procedures\n" + self._format_procedures(matched),
            ),
            ContextLayer(
                name="compaction",
                priority=10,
                content=(
                    "# Compaction Summary\n"
                    + (str(compaction.get("summary", "")).strip() or "(none)")
                ),
            ),
        ]

        # --- apply token budget with priority-based dropping ---
        included, dropped = self._apply_budget(layers)

        # --- record debug info ---
        if self.debug:
            info = BuildInfo(budget=self.token_budget)
            for layer in included:
                info.included.append(
                    {"name": layer.name, "tokens": layer.tokens, "priority": layer.priority}
                )
            for layer in dropped:
                info.dropped.append(
                    {"name": layer.name, "tokens": layer.tokens, "priority": layer.priority}
                )
            info.total_tokens = sum(l.tokens for l in included)
            self.last_build_info = info
            logger.debug(
                "context build: %d layers included (%d tokens), %d dropped, budget=%d",
                len(included),
                info.total_tokens,
                len(dropped),
                self.token_budget,
            )

        # --- assemble in display order ---
        layer_map = {layer.name: layer for layer in included}
        parts: List[str] = []
        for name in _DISPLAY_ORDER:
            layer = layer_map.get(name)
            if layer and layer.content:
                parts.append(layer.content)

        return "\n\n".join(parts).strip() + "\n"

    def _apply_budget(
        self, layers: List[ContextLayer]
    ) -> tuple[List[ContextLayer], List[ContextLayer]]:
        """Drop lowest-priority (highest priority number) layers until under budget."""
        total = sum(l.tokens for l in layers)
        if total <= self.token_budget:
            return layers, []

        # Sort by priority descending (lowest importance first) for dropping
        droppable = sorted(
            [l for l in layers if not l.required],
            key=lambda l: l.priority,
            reverse=True,
        )
        dropped: List[ContextLayer] = []
        for layer in droppable:
            if total <= self.token_budget:
                break
            total -= layer.tokens
            dropped.append(layer)

        dropped_names = {l.name for l in dropped}
        included = [l for l in layers if l.name not in dropped_names]
        return included, dropped

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
