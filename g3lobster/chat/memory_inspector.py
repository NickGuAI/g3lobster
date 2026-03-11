"""Memory query detection and card assembly for the memory inspector.

Intercepts natural-language memory queries (e.g. "what do you remember about me?")
and assembles a Cards v2 payload from MemoryManager, GlobalMemoryManager, and metrics.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from g3lobster.chat.cards import build_memory_inspector_card

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry
    from g3lobster.memory.global_memory import GlobalMemoryManager

# Patterns that indicate a user is asking about agent memory.
MEMORY_QUERY_PATTERNS = [
    re.compile(r"what\s+do\s+you\s+remember", re.IGNORECASE),
    re.compile(r"what\s+(?:have\s+you|do\s+you)\s+(?:learned|learnt)", re.IGNORECASE),
    re.compile(r"what\s+(?:procedures?|procs?)\s+(?:have\s+you|do\s+you)", re.IGNORECASE),
    re.compile(r"show\s+(?:my\s+)?(?:preferences|memory|memories)", re.IGNORECASE),
    re.compile(r"my\s+memory", re.IGNORECASE),
    re.compile(r"what\s+do\s+you\s+know\s+about\s+me", re.IGNORECASE),
    re.compile(r"what\s+(?:have\s+you\s+)?stored\s+about\s+me", re.IGNORECASE),
    re.compile(r"memory\s+inspector", re.IGNORECASE),
]


def detect_memory_query(text: str) -> Optional[str]:
    """Return a query-type string if text is a memory query, else None.

    Returns ``"memory_query"`` for any match so the caller knows to build
    a memory inspector card.
    """
    cleaned = text.strip()
    if not cleaned:
        return None
    for pattern in MEMORY_QUERY_PATTERNS:
        if pattern.search(cleaned):
            return "memory_query"
    return None


def _gather_preferences(
    agent_id: str,
    registry: "AgentRegistry",
    global_memory: Optional["GlobalMemoryManager"],
    user_id: str,
) -> List[str]:
    """Collect user-preference entries from per-user and agent memory."""
    prefs: List[str] = []

    # Per-user memory from GlobalMemoryManager
    if global_memory:
        user_mem = global_memory.read_user_memory_for(user_id)
        if user_mem and user_mem.strip() not in ("# USER", "# USER\n"):
            # Extract sections after the header
            for section in user_mem.split("## ")[1:]:
                content = section.strip()
                if content:
                    prefs.append(content[:300])

    # Agent-level tagged preferences
    runtime = registry.get_agent(agent_id)
    if runtime:
        mm = runtime.memory_manager
        pref_entries = mm.get_memories_by_tag("user preference")
        prefs.extend(entry[:300] for entry in pref_entries[:10])

    return prefs[:10]


def _gather_procedures(
    agent_id: str,
    registry: "AgentRegistry",
    global_memory: Optional["GlobalMemoryManager"],
) -> List[Dict[str, Any]]:
    """Collect procedures from agent and global stores."""
    procedures: List[Dict[str, Any]] = []

    runtime = registry.get_agent(agent_id)
    if runtime:
        mm = runtime.memory_manager
        for proc in mm.list_procedures():
            procedures.append({
                "title": proc.title,
                "weight": proc.weight,
                "status": proc.status,
                "steps": proc.steps,
                "source": "agent",
            })

        # Also include usable candidates
        usable = mm.candidate_store.list_usable()
        for proc in usable:
            # Skip duplicates already from permanent store
            existing_titles = {p["title"] for p in procedures}
            if proc.title not in existing_titles:
                procedures.append({
                    "title": proc.title,
                    "weight": proc.effective_weight,
                    "status": proc.status,
                    "steps": proc.steps,
                    "source": "candidate",
                })

    # Global procedures
    if global_memory:
        for proc in global_memory.procedures.list_procedures():
            existing_titles = {p["title"] for p in procedures}
            if proc.title not in existing_titles:
                procedures.append({
                    "title": proc.title,
                    "weight": proc.weight,
                    "status": proc.status,
                    "steps": proc.steps,
                    "source": "global",
                })

    # Sort by weight descending
    procedures.sort(key=lambda p: p.get("weight", 0), reverse=True)
    return procedures[:10]


def _gather_daily_notes(agent_id: str, registry: "AgentRegistry") -> List[str]:
    """Return summaries of the last 5 daily notes."""
    runtime = registry.get_agent(agent_id)
    if not runtime:
        return []

    mm = runtime.memory_manager
    notes: List[str] = []
    today = date.today()
    for offset in range(30):  # look back up to 30 days
        day = today - timedelta(days=offset)
        path = mm.daily_note_path(day)
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                # Show date + first 200 chars
                preview = content[:200] + ("..." if len(content) > 200 else "")
                notes.append(f"*{day.isoformat()}*: {preview}")
        if len(notes) >= 5:
            break

    return notes


def _gather_stats(agent_id: str, registry: "AgentRegistry") -> Dict[str, Any]:
    """Compute basic agent stats from memory files."""
    runtime = registry.get_agent(agent_id)
    if not runtime:
        return {}

    mm = runtime.memory_manager

    session_ids = mm.sessions.list_sessions()
    total_messages = 0
    for sid in session_ids:
        total_messages += mm.sessions.message_count(sid)

    memory_dir = Path(mm.data_dir) / ".memory"
    memory_file = memory_dir / "MEMORY.md"
    memory_bytes = memory_file.stat().st_size if memory_file.exists() else 0

    procedures_count = len(mm.list_procedures())
    daily_dir = memory_dir / "daily"
    daily_notes_count = len(list(daily_dir.glob("*.md"))) if daily_dir.exists() else 0

    return {
        "total_sessions": len(session_ids),
        "total_messages": total_messages,
        "memory_bytes": memory_bytes,
        "procedures_count": procedures_count,
        "daily_notes_count": daily_notes_count,
    }


async def build_memory_card(
    agent_id: str,
    user_id: str,
    registry: "AgentRegistry",
    global_memory: Optional["GlobalMemoryManager"] = None,
) -> Dict[str, Any]:
    """Gather all memory data and return a ``cardsV2`` dict."""
    runtime = registry.get_agent(agent_id)
    agent_name = "Agent"
    agent_emoji = "\U0001f916"
    if runtime:
        agent_name = runtime.persona.name
        agent_emoji = runtime.persona.emoji

    preferences = _gather_preferences(agent_id, registry, global_memory, user_id)
    procedures = _gather_procedures(agent_id, registry, global_memory)
    daily_notes = _gather_daily_notes(agent_id, registry)
    stats = _gather_stats(agent_id, registry)

    cards_v2 = build_memory_inspector_card(
        agent_name=agent_name,
        agent_emoji=agent_emoji,
        preferences=preferences,
        procedures=procedures,
        daily_notes=daily_notes,
        stats=stats,
    )

    return {"cardsV2": cards_v2}
