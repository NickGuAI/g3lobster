"""Named agent management routes."""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from g3lobster.agents.persona import (
    AgentPersona,
    agent_dir,
    delete_persona,
    ensure_unique_agent_id,
    is_reserved_agent_id,
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
    MemoryResponse,
    MemoryUpdateRequest,
    SessionListResponse,
    TestAgentRequest,
)
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_agent_response(payload: Dict[str, object]) -> AgentResponse:
    return AgentResponse(**payload)


async def _status_map(request: Request) -> Dict[str, Dict[str, object]]:
    registry = request.app.state.registry
    payload = await registry.status()
    return {item["id"]: item for item in payload.get("agents", [])}


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


@router.get("", response_model=List[AgentResponse])
async def list_agents(request: Request) -> List[AgentResponse]:
    items = await _status_map(request)
    return [_to_agent_response(items[key]) for key in sorted(items.keys())]


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

    persona = AgentPersona(
        id=agent_id,
        name=payload.name,
        emoji=payload.emoji,
        soul=payload.soul,
        model=payload.model,
        mcp_servers=payload.mcp_servers,
        enabled=payload.enabled,
    )
    saved = save_persona(config.agents.data_dir, persona)

    status_payload = await _status_map(request)
    current = status_payload.get(agent_id) or {
        "id": saved.id,
        "name": saved.name,
        "emoji": saved.emoji,
        "enabled": saved.enabled,
        "model": saved.model,
        "mcp_servers": list(saved.mcp_servers),
        "bot_user_id": saved.bot_user_id,
        "state": "stopped",
        "uptime_s": 0,
        "current_task": None,
        "pending_assignments": 0,
        "description": (saved.soul.split("\n")[0].strip().lstrip("#").strip() if saved.soul else ""),
    }
    return AgentDetailResponse(
        **current,
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


@router.get("/{agent_id}", response_model=AgentDetailResponse)
async def get_agent(agent_id: str, request: Request) -> AgentDetailResponse:
    config = request.app.state.config
    persona = _ensure_persona(config.agents.data_dir, agent_id)

    status_payload = await _status_map(request)
    current = status_payload.get(agent_id) or {
        "id": persona.id,
        "name": persona.name,
        "emoji": persona.emoji,
        "enabled": persona.enabled,
        "model": persona.model,
        "mcp_servers": list(persona.mcp_servers),
        "bot_user_id": persona.bot_user_id,
        "state": "stopped",
        "uptime_s": 0,
        "current_task": None,
        "pending_assignments": 0,
        "description": (persona.soul.split("\n")[0].strip().lstrip("#").strip() if persona.soul else ""),
    }
    return AgentDetailResponse(
        **current,
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

    refreshed = await get_agent(agent_id, request)
    return refreshed


@router.delete("/{agent_id}")
async def delete_agent_route(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    registry = request.app.state.registry

    _ensure_persona(config.agents.data_dir, agent_id)
    await registry.stop_agent(agent_id)

    deleted = delete_persona(config.agents.data_dir, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"deleted": True}


@router.post("/{agent_id}/start")
async def start_agent(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    started = await registry.start_agent(agent_id)
    if not started:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"started": True}


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str, request: Request) -> dict:
    config = request.app.state.config
    _ensure_persona(config.agents.data_dir, agent_id)

    registry = request.app.state.registry
    await registry.stop_agent(agent_id)
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

    chat_bridge = request.app.state.chat_bridge
    if not chat_bridge:
        raise HTTPException(status_code=400, detail="Chat bridge is not running")

    message = f"{persona.emoji} {persona.name} test: {payload.text}"
    await chat_bridge.send_message(message)
    return {"sent": True}
