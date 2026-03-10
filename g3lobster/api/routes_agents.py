"""Named agent management routes."""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from g3lobster.agents.persona import (
    AgentPersona,
    agent_dir,
    delete_persona,
    ensure_unique_agent_id,
    is_reserved_agent_id,
    list_personas,
    load_persona,
    save_persona,
    slugify_agent_id,
)
from g3lobster.api.models import (
    AgentCreateRequest,
    AgentDetailResponse,
    AgentResponse,
    AgentUpdateRequest,
    KnowledgeListResponse,
    LinkBotRequest,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    MemoryResponse,
    MemoryUpdateRequest,
    SessionListResponse,
    SleepAgentRequest,
    SubAgentRequest,
    SubAgentResponse,
    TaskDetailResponse,
    TaskEventResponse,
    TaskListResponse,
    TaskSummaryResponse,
    TestAgentRequest,
)
from g3lobster.config import normalize_space_id
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine
from g3lobster.tasks.types import Task, TaskStatus

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_agent_response(payload: Dict[str, object]) -> AgentResponse:
    return AgentResponse(**payload)


async def _status_map(request: Request) -> Dict[str, Dict[str, object]]:
    registry = request.app.state.registry
    payload = await registry.status()
    return {item["id"]: item for item in payload.get("agents", [])}


def _bridge_status_map(request: Request) -> Dict[str, Dict[str, object]]:
    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if not bridge_manager:
        return {}
    return {item["agent_id"]: item for item in bridge_manager.status()}


def _hydrate_agent_payload(
    current: Dict[str, object],
    persona: AgentPersona,
    bridge_item: Optional[Dict[str, object]],
) -> Dict[str, object]:
    payload = dict(current)
    payload["space_id"] = persona.space_id
    payload["bridge_enabled"] = bool(persona.bridge_enabled)
    payload["bridge_running"] = bool(bridge_item and bridge_item.get("is_running"))
    payload.setdefault("bot_user_id", persona.bot_user_id)
    payload.setdefault("dm_allowlist", list(persona.dm_allowlist))
    return payload


def _memory_manager(request: Request, agent_id: str) -> MemoryManager:
    registry = request.app.state.registry
    runtime = registry.get_agent(agent_id)
    if runtime:
        return runtime.memory_manager

    # Cache managers for stopped agents so concurrent requests share
    # the same SessionStore (and its locks) instead of each getting an
    # independent instance that could corrupt JSONL files.
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


def _ensure_persona(data_dir: str, agent_id: str) -> AgentPersona:
    try:
        persona = load_persona(data_dir, agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent id format")
    if not persona:
        raise HTTPException(status_code=404, detail="Agent not found")
    return persona


def _global_memory_manager(request: Request) -> GlobalMemoryManager:
    manager = request.app.state.global_memory_manager
    if manager is None:
        raise RuntimeError("GlobalMemoryManager is not initialized on app state")
    return manager


def _memory_search_engine(request: Request) -> MemorySearchEngine:
    config = request.app.state.config
    return MemorySearchEngine(data_dir=config.agents.data_dir)


def _to_task_summary(task: Task) -> TaskSummaryResponse:
    payload = task.as_dict()
    return TaskSummaryResponse(**{key: payload[key] for key in TaskSummaryResponse.model_fields.keys()})


def _to_task_detail(task: Task) -> TaskDetailResponse:
    payload = task.as_dict()
    event_items = [
        TaskEventResponse(
            timestamp=event.timestamp,
            kind=event.kind,
            payload=dict(event.payload),
        )
        for event in task.events
    ]
    summary = {key: payload[key] for key in TaskSummaryResponse.model_fields.keys()}
    return TaskDetailResponse(**summary, events=event_items)


def _to_subagent_response(run: object) -> SubAgentResponse:
    if hasattr(run, "as_dict"):
        payload = run.as_dict()
    elif isinstance(run, dict):
        payload = dict(run)
    else:
        payload = {
            "session_name": getattr(run, "session_name", ""),
            "agent_id": getattr(run, "agent_id", ""),
            "prompt": getattr(run, "prompt", ""),
            "mcp_server_names": list(getattr(run, "mcp_server_names", [])),
            "parent_task_id": getattr(run, "parent_task_id", None),
            "status": getattr(run, "status", "unknown"),
            "created_at": float(getattr(run, "created_at", 0.0)),
            "started_at": float(getattr(run, "started_at", 0.0)),
            "completed_at": getattr(run, "completed_at", None),
            "timeout_s": float(getattr(run, "timeout_s", 0.0)),
            "output": getattr(run, "output", None),
            "error": getattr(run, "error", None),
        }
    return SubAgentResponse(**payload)


@router.get("", response_model=List[AgentResponse])
async def list_agents(request: Request) -> List[AgentResponse]:
    config = request.app.state.config
    items = await _status_map(request)
    bridge_items = _bridge_status_map(request)

    payloads: List[AgentResponse] = []
    for persona in list_personas(config.agents.data_dir):
        current = items.get(persona.id) or {
            "id": persona.id,
            "name": persona.name,
            "emoji": persona.emoji,
            "enabled": persona.enabled,
            "model": persona.model,
            "mcp_servers": list(persona.mcp_servers),
            "bot_user_id": persona.bot_user_id,
            "dm_allowlist": list(persona.dm_allowlist),
            "state": "stopped",
            "uptime_s": 0,
            "current_task": None,
            "pending_assignments": 0,
            "description": (persona.soul.split("\n")[0].strip().lstrip("#").strip() if persona.soul else ""),
        }
        payloads.append(
            _to_agent_response(
                _hydrate_agent_payload(current, persona, bridge_items.get(persona.id))
            )
        )

    return payloads


@router.post("", response_model=AgentDetailResponse)
async def create_agent(payload: AgentCreateRequest, request: Request) -> AgentDetailResponse:
    config = request.app.state.config
    base_id = slugify_agent_id(payload.name)
    if is_reserved_agent_id(base_id):
        raise HTTPException(status_code=422, detail=f"Agent id '{base_id}' is reserved")
    try:
        agent_id = ensure_unique_agent_id(config.agents.data_dir, base_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if "space_id" in payload.model_fields_set:
        space_id = normalize_space_id(payload.space_id)
    else:
        space_id = normalize_space_id(config.chat.space_id)
    if "bridge_enabled" in payload.model_fields_set:
        bridge_enabled = bool(payload.bridge_enabled)
    else:
        bridge_enabled = bool(space_id)

    persona = AgentPersona(
        id=agent_id,
        name=payload.name,
        emoji=payload.emoji,
        soul=payload.soul,
        model=payload.model,
        mcp_servers=payload.mcp_servers,
        enabled=payload.enabled,
        dm_allowlist=list(payload.dm_allowlist),
        space_id=space_id,
        bridge_enabled=bridge_enabled,
    )
    saved = save_persona(config.agents.data_dir, persona)

    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if bridge_manager and config.chat.enabled and saved.bridge_enabled and saved.space_id:
        await bridge_manager.start_bridge(saved.id)

    status_payload = await _status_map(request)
    bridge_item = _bridge_status_map(request).get(saved.id)
    current = status_payload.get(agent_id) or {
        "id": saved.id,
        "name": saved.name,
        "emoji": saved.emoji,
        "enabled": saved.enabled,
        "model": saved.model,
        "mcp_servers": list(saved.mcp_servers),
        "bot_user_id": saved.bot_user_id,
        "dm_allowlist": list(saved.dm_allowlist),
        "space_id": saved.space_id,
        "bridge_enabled": saved.bridge_enabled,
        "bridge_running": bool(bridge_item and bridge_item.get("is_running")),
        "state": "stopped",
        "uptime_s": 0,
        "current_task": None,
        "pending_assignments": 0,
        "recent_tasks": 0,
        "description": (saved.soul.split("\n")[0].strip().lstrip("#").strip() if saved.soul else ""),
    }
    return AgentDetailResponse(
        **_hydrate_agent_payload(current, saved, bridge_item),
        soul=saved.soul,
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


@router.get("/_global/user-memory", response_model=MemoryResponse)
async def get_global_user_memory(request: Request) -> MemoryResponse:
    manager = _global_memory_manager(request)
    return MemoryResponse(content=manager.read_user_memory())


@router.put("/_global/user-memory")
async def update_global_user_memory(payload: MemoryUpdateRequest, request: Request) -> dict:
    manager = _global_memory_manager(request)
    manager.write_user_memory(payload.content)
    return {"updated": True}


@router.get("/_global/user-memory/{user_id}", response_model=MemoryResponse)
async def get_per_user_memory(user_id: str, request: Request) -> MemoryResponse:
    manager = _global_memory_manager(request)
    return MemoryResponse(content=manager.read_user_memory_for(user_id))


@router.put("/_global/user-memory/{user_id}")
async def update_per_user_memory(user_id: str, payload: MemoryUpdateRequest, request: Request) -> dict:
    manager = _global_memory_manager(request)
    manager.write_user_memory_for(user_id, payload.content)
    return {"updated": True}


@router.get("/_global/procedures", response_model=MemoryResponse)
async def get_global_procedures(request: Request) -> MemoryResponse:
    manager = _global_memory_manager(request)
    return MemoryResponse(content=manager.read_procedures())


@router.put("/_global/procedures")
async def update_global_procedures(payload: MemoryUpdateRequest, request: Request) -> dict:
    manager = _global_memory_manager(request)
    try:
        manager.write_procedures(payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"updated": True}


@router.get("/_global/knowledge", response_model=KnowledgeListResponse)
async def list_global_knowledge(request: Request) -> KnowledgeListResponse:
    manager = _global_memory_manager(request)
    return KnowledgeListResponse(items=manager.list_knowledge())


@router.get("/_mcp/servers")
async def list_mcp_servers(request: Request) -> dict:
    config = request.app.state.config
    from g3lobster.mcp.loader import MCPConfigLoader
    loader = MCPConfigLoader(config_dir=config.mcp.config_dir)
    names = sorted(loader.load_all().keys())
    return {"servers": names}


@router.get("/{agent_id}", response_model=AgentDetailResponse)
async def get_agent(agent_id: str, request: Request) -> AgentDetailResponse:
    config = request.app.state.config
    persona = _ensure_persona(config.agents.data_dir, agent_id)

    status_payload = await _status_map(request)
    bridge_item = _bridge_status_map(request).get(agent_id)
    current = status_payload.get(agent_id) or {
        "id": persona.id,
        "name": persona.name,
        "emoji": persona.emoji,
        "enabled": persona.enabled,
        "model": persona.model,
        "mcp_servers": list(persona.mcp_servers),
        "bot_user_id": persona.bot_user_id,
        "dm_allowlist": list(persona.dm_allowlist),
        "space_id": persona.space_id,
        "bridge_enabled": persona.bridge_enabled,
        "bridge_running": bool(bridge_item and bridge_item.get("is_running")),
        "state": "stopped",
        "uptime_s": 0,
        "current_task": None,
        "pending_assignments": 0,
        "recent_tasks": 0,
        "description": (persona.soul.split("\n")[0].strip().lstrip("#").strip() if persona.soul else ""),
    }
    return AgentDetailResponse(
        **_hydrate_agent_payload(current, persona, bridge_item),
        soul=persona.soul,
        created_at=persona.created_at,
        updated_at=persona.updated_at,
    )


@router.put("/{agent_id}", response_model=AgentDetailResponse)
async def update_agent(agent_id: str, payload: AgentUpdateRequest, request: Request) -> AgentDetailResponse:
    config = request.app.state.config
    registry = request.app.state.registry

    persona = _ensure_persona(config.agents.data_dir, agent_id)
    updates = payload.model_dump(exclude_unset=True)

    changed_keys = set(updates.keys())

    if "name" in updates:
        persona.name = str(updates["name"])
    if "emoji" in updates:
        persona.emoji = str(updates["emoji"])
    if "soul" in updates:
        persona.soul = str(updates["soul"])
    if "model" in updates:
        persona.model = str(updates["model"])
    if "mcp_servers" in updates:
        persona.mcp_servers = [str(item) for item in (updates["mcp_servers"] or ["*"])]
    if "enabled" in updates:
        persona.enabled = bool(updates["enabled"])
    if "bot_user_id" in updates:
        raw = updates["bot_user_id"]
        persona.bot_user_id = str(raw).strip() if raw else None
    if "dm_allowlist" in updates:
        persona.dm_allowlist = [str(item) for item in (updates["dm_allowlist"] or [])]
    if "space_id" in updates:
        persona.space_id = normalize_space_id(updates["space_id"])
    if "bridge_enabled" in updates:
        persona.bridge_enabled = bool(updates["bridge_enabled"])

    saved = save_persona(config.agents.data_dir, persona)

    runtime = registry.get_agent(agent_id)
    if runtime:
        runtime.persona = saved
        runtime.context_builder.system_preamble = saved.soul

    if "enabled" in changed_keys and not saved.enabled:
        await registry.stop_agent(agent_id)
    elif "enabled" in changed_keys and saved.enabled and not runtime:
        await registry.start_agent(agent_id)
    elif runtime and ({"model", "mcp_servers"} & changed_keys):
        await registry.restart_agent(agent_id)

    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if bridge_manager and ({"enabled", "bridge_enabled", "space_id"} & changed_keys):
        if not saved.enabled or not saved.bridge_enabled or not saved.space_id:
            await bridge_manager.stop_bridge(agent_id)
        elif config.chat.enabled:
            await bridge_manager.start_bridge(agent_id)

    refreshed = await get_agent(agent_id, request)
    return refreshed


@router.delete("/{agent_id}")
async def delete_agent_route(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    registry = request.app.state.registry
    bridge_manager = getattr(request.app.state, "bridge_manager", None)

    _ensure_persona(config.agents.data_dir, agent_id)
    await registry.stop_agent(agent_id)
    if bridge_manager:
        await bridge_manager.stop_bridge(agent_id)

    deleted = delete_persona(config.agents.data_dir, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"deleted": True}


@router.post("/{agent_id}/start")
async def start_agent(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    persona = _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    started = await registry.start_agent(agent_id)
    if not started:
        raise HTTPException(status_code=404, detail="Agent not found")

    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if bridge_manager and config.chat.enabled and persona.bridge_enabled and persona.space_id:
        await bridge_manager.start_bridge(agent_id)
    return {"started": True}


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    await registry.stop_agent(agent_id)
    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if bridge_manager:
        await bridge_manager.stop_bridge(agent_id)
    return {"stopped": True}


@router.post("/{agent_id}/restart")
async def restart_agent(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    restarted = await registry.restart_agent(agent_id)
    if not restarted:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"restarted": True}


@router.post("/{agent_id}/sleep")
async def sleep_agent_route(agent_id: str, payload: SleepAgentRequest, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    slept = await registry.sleep_agent(agent_id, payload.duration_s)
    if not slept:
        raise HTTPException(status_code=404, detail="Agent not found or not running")
    return {"sleeping": True, "duration_s": payload.duration_s}


@router.get("/{agent_id}/tasks", response_model=TaskListResponse)
async def list_agent_tasks(
    agent_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> TaskListResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)
    registry = request.app.state.registry
    tasks = registry.list_tasks(agent_id, limit=limit)
    return TaskListResponse(tasks=[_to_task_summary(task) for task in tasks])


@router.get("/{agent_id}/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_agent_task(agent_id: str, task_id: str, request: Request) -> TaskDetailResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)
    registry = request.app.state.registry
    task = registry.get_task(agent_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _to_task_detail(task)


@router.post("/{agent_id}/tasks/{task_id}/cancel", response_model=TaskDetailResponse)
async def cancel_agent_task(agent_id: str, task_id: str, request: Request) -> TaskDetailResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)
    registry = request.app.state.registry
    task = await registry.cancel_task(agent_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.CANCELED:
        raise HTTPException(status_code=409, detail=f"Task is not cancelable (status={task.status.value})")
    return _to_task_detail(task)


@router.post("/memory/search", response_model=MemorySearchResponse)
async def search_memory(payload: MemorySearchRequest, request: Request) -> MemorySearchResponse:
    config = request.app.state.config
    if payload.agent_id:
        _ensure_persona(config.agents.data_dir, payload.agent_id)

    engine = _memory_search_engine(request)
    agent_ids = [payload.agent_id] if payload.agent_id else None
    hits = engine.search(
        payload.query,
        agent_ids=agent_ids,
        memory_types=payload.memory_types or None,
        start_date=payload.start_date,
        end_date=payload.end_date,
        limit=payload.limit,
    )
    return MemorySearchResponse(results=[MemorySearchResult(**hit.as_dict()) for hit in hits])


@router.get("/{agent_id}/memory/search", response_model=MemorySearchResponse)
async def search_agent_memory(
    agent_id: str,
    request: Request,
    q: str = Query(min_length=1),
    memory_type: List[str] = Query(default=[]),
    start_date: str = Query(default=""),
    end_date: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=200),
) -> MemorySearchResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    engine = _memory_search_engine(request)
    selected_types = memory_type or ["memory", "procedures", "daily", "session"]
    hits = engine.search(
        q,
        agent_ids=[agent_id],
        memory_types=selected_types,
        start_date=start_date or None,
        end_date=end_date or None,
        limit=limit,
    )
    return MemorySearchResponse(results=[MemorySearchResult(**hit.as_dict()) for hit in hits])


@router.post("/{agent_id}/subagents", response_model=SubAgentResponse)
async def spawn_subagent(agent_id: str, payload: SubAgentRequest, request: Request) -> SubAgentResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    try:
        run = await registry.spawn_subagent(
            agent_id=agent_id,
            prompt=payload.prompt,
            timeout_s=payload.timeout_s,
            mcp_servers=payload.mcp_servers,
            parent_task_id=payload.parent_task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_subagent_response(run)


@router.get("/{agent_id}/subagents", response_model=List[SubAgentResponse])
async def list_subagents(
    agent_id: str,
    request: Request,
    active_only: bool = Query(default=True),
) -> List[SubAgentResponse]:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    runs = await registry.list_subagents(agent_id=agent_id, active_only=active_only)
    return [_to_subagent_response(run) for run in runs]


@router.delete("/{agent_id}/subagents/{session_name}", response_model=SubAgentResponse)
async def kill_subagent(agent_id: str, session_name: str, request: Request) -> SubAgentResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    run = await registry.kill_subagent(agent_id=agent_id, session_name=session_name)
    if run is None:
        raise HTTPException(status_code=404, detail="Sub-agent session not found")
    return _to_subagent_response(run)


@router.get("/{agent_id}/memory", response_model=MemoryResponse)
async def get_memory(agent_id: str, request: Request) -> MemoryResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    return MemoryResponse(content=manager.read_memory())


@router.put("/{agent_id}/memory")
async def update_memory(agent_id: str, payload: MemoryUpdateRequest, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    manager.write_memory(payload.content)
    return {"updated": True}


@router.post("/{agent_id}/memory/tags/{tag}")
async def append_tagged_memory(agent_id: str, tag: str, payload: MemoryUpdateRequest, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    try:
        manager.append_tagged_memory(tag=tag, content=payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"updated": True}


@router.get("/{agent_id}/memory/tags/{tag}")
async def get_tagged_memory(agent_id: str, tag: str, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    return {"tag": tag, "entries": manager.get_memories_by_tag(tag)}


@router.get("/{agent_id}/procedures", response_model=MemoryResponse)
async def get_agent_procedures(agent_id: str, request: Request) -> MemoryResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    return MemoryResponse(content=manager.read_procedures())


@router.put("/{agent_id}/procedures")
async def update_agent_procedures(agent_id: str, payload: MemoryUpdateRequest, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    try:
        manager.write_procedures(payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"updated": True}


@router.get("/{agent_id}/sessions", response_model=SessionListResponse)
async def list_agent_sessions(agent_id: str, request: Request) -> SessionListResponse:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    return SessionListResponse(sessions=manager.list_sessions())


@router.get("/{agent_id}/sessions/{session_id}")
async def get_agent_session(agent_id: str, session_id: str, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    manager = _memory_manager(request, agent_id)
    entries = manager.read_session(session_id)
    if not entries:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "entries": entries}


@router.post("/{agent_id}/link-bot")
async def link_agent_bot(agent_id: str, payload: LinkBotRequest, request: Request) -> dict:
    config = request.app.state.config
    registry = request.app.state.registry

    persona = _ensure_persona(config.agents.data_dir, agent_id)
    persona.bot_user_id = payload.bot_user_id.strip()
    saved = save_persona(config.agents.data_dir, persona)

    runtime = registry.get_agent(agent_id)
    if runtime:
        runtime.persona = saved

    return {"linked": True, "bot_user_id": saved.bot_user_id}


@router.post("/{agent_id}/test")
async def test_agent(agent_id: str, payload: TestAgentRequest, request: Request) -> dict:
    config = request.app.state.config
    persona = _ensure_persona(config.agents.data_dir, agent_id)

    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    chat_bridge = bridge_manager.get_bridge(agent_id) if bridge_manager else request.app.state.chat_bridge
    if not chat_bridge:
        raise HTTPException(status_code=400, detail="Chat bridge is not running")

    message = f"{persona.emoji} {persona.name} test: {payload.text}"
    await chat_bridge.send_message(message)
    return {"sent": True}
