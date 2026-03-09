"""Per-agent activity metrics routes."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from g3lobster.agents.persona import agent_dir, list_personas, load_persona
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.sessions import SessionStore

router = APIRouter(prefix="/agents", tags=["metrics"])

# Simple TTL cache: {cache_key: (timestamp, data)}
_cache: Dict[str, tuple] = {}
_CACHE_TTL_S = 60


def _cached(key: str):
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL_S:
        return entry[1]
    return None


def _set_cache(key: str, data: Any) -> None:
    _cache[key] = (time.monotonic(), data)


def _memory_manager(request: Request, agent_id: str) -> MemoryManager:
    registry = request.app.state.registry
    runtime = registry.get_agent(agent_id)
    if runtime:
        return runtime.memory_manager

    cache: dict = request.app.state._stopped_memory_managers
    cached = cache.get(agent_id)
    if cached is not None:
        return cached

    config = request.app.state.config
    data_dir = str(agent_dir(config.agents.data_dir, agent_id))
    manager = MemoryManager(
        data_dir=data_dir,
        compact_threshold=config.agents.compact_threshold,
        compact_keep_ratio=config.agents.compact_keep_ratio,
        compact_chunk_size=config.agents.compact_chunk_size,
        procedure_min_frequency=config.agents.procedure_min_frequency,
        memory_max_sections=config.agents.memory_max_sections,
        gemini_command=config.gemini.command,
        gemini_args=config.gemini.args,
        gemini_timeout_s=config.gemini.response_timeout_s,
        gemini_cwd=config.gemini.workspace_dir,
    )
    cache[agent_id] = manager
    return manager


def _compute_metrics(agent_id: str, manager: MemoryManager) -> Dict[str, Any]:
    """Compute metrics from session JSONL files and memory files."""
    sessions = manager.sessions
    session_ids = sessions.list_sessions()

    total_sessions = len(session_ids)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    active_today = 0

    total_messages = 0
    user_messages = 0
    assistant_messages = 0
    compaction_events = 0

    response_times: List[float] = []

    for sid in session_ids:
        path = sessions._session_path(sid)
        session_active_today = False
        last_user_ts: Optional[float] = None

        for entry in SessionStore._iter_entries(path):
            entry_type = entry.get("type")
            ts_str = entry.get("timestamp", "")

            if entry_type == "compaction":
                compaction_events += 1
                continue

            if entry_type != "message":
                continue

            total_messages += 1
            role = entry.get("message", {}).get("role", "")
            if role == "user":
                user_messages += 1
            elif role == "assistant":
                assistant_messages += 1

            # Parse timestamp for today check and response time
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts_epoch = ts_dt.timestamp()
                except (ValueError, AttributeError):
                    ts_epoch = None
                    ts_dt = None

                if ts_dt and ts_dt.strftime("%Y-%m-%d") == today_str:
                    session_active_today = True

                # Track response times: user -> assistant pairs
                if role == "user" and ts_epoch is not None:
                    last_user_ts = ts_epoch
                elif role == "assistant" and last_user_ts is not None and ts_epoch is not None:
                    delta = ts_epoch - last_user_ts
                    if 0 < delta < 600:  # sanity: less than 10 minutes
                        response_times.append(delta)
                    last_user_ts = None

        if session_active_today:
            active_today += 1

    # Response time stats
    avg_s = 0.0
    p95_s = 0.0
    if response_times:
        response_times.sort()
        avg_s = round(sum(response_times) / len(response_times), 1)
        p95_idx = int(len(response_times) * 0.95)
        p95_s = round(response_times[min(p95_idx, len(response_times) - 1)], 1)

    # Memory stats
    memory_dir = Path(manager.data_dir) / ".memory"
    memory_md_path = memory_dir / "MEMORY.md"
    procedures_path = memory_dir / "PROCEDURES.md"
    candidates_path = memory_dir / "CANDIDATES.json"
    daily_dir = memory_dir / "daily"

    memory_md_bytes = memory_md_path.stat().st_size if memory_md_path.exists() else 0

    procedures_count = 0
    if procedures_path.exists():
        content = procedures_path.read_text(encoding="utf-8")
        procedures_count = content.count("## Procedure:")

    candidate_count = 0
    if candidates_path.exists():
        try:
            candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
            candidate_count = len(candidates) if isinstance(candidates, list) else 0
        except (json.JSONDecodeError, OSError):
            pass

    daily_notes = len(list(daily_dir.glob("*.md"))) if daily_dir.exists() else 0

    return {
        "agent_id": agent_id,
        "sessions": {
            "total": total_sessions,
            "active_today": active_today,
        },
        "messages": {
            "total": total_messages,
            "user": user_messages,
            "assistant": assistant_messages,
            "compaction_events": compaction_events,
        },
        "response_time": {
            "samples": len(response_times),
            "avg_s": avg_s,
            "p95_s": p95_s,
        },
        "memory": {
            "memory_md_bytes": memory_md_bytes,
            "procedures_count": procedures_count,
            "candidate_count": candidate_count,
            "daily_notes": daily_notes,
        },
    }


@router.get("/metrics/summary")
async def metrics_summary(request: Request) -> dict:
    cached = _cached("metrics_summary")
    if cached is not None:
        return cached

    config = request.app.state.config
    personas = list_personas(config.agents.data_dir)
    summary = []
    for persona in personas:
        agent_cache_key = f"metrics:{persona.id}"
        agent_data = _cached(agent_cache_key)
        if agent_data is None:
            manager = _memory_manager(request, persona.id)
            agent_data = _compute_metrics(persona.id, manager)
            _set_cache(agent_cache_key, agent_data)
        summary.append({
            "agent_id": persona.id,
            "name": persona.name,
            "emoji": persona.emoji,
            "sessions_total": agent_data["sessions"]["total"],
            "messages_total": agent_data["messages"]["total"],
            "avg_response_s": agent_data["response_time"]["avg_s"],
        })

    result = {"agents": summary}
    _set_cache("metrics_summary", result)
    return result


@router.get("/{agent_id}/metrics")
async def agent_metrics(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    persona = load_persona(config.agents.data_dir, agent_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Agent not found")

    cache_key = f"metrics:{agent_id}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    manager = _memory_manager(request, agent_id)
    data = _compute_metrics(agent_id, manager)
    _set_cache(cache_key, data)
    return data
