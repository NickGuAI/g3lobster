"""Pydantic API models."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    emoji: str = "🤖"
    soul: str = ""
    model: str = "gemini"
    mcp_servers: List[str] = Field(default_factory=lambda: ["*"])
    enabled: bool = True
    dm_allowlist: List[str] = Field(default_factory=list)
    space_id: Optional[str] = None
    bridge_enabled: bool = False


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    emoji: Optional[str] = None
    soul: Optional[str] = None
    model: Optional[str] = None
    mcp_servers: Optional[List[str]] = None
    enabled: Optional[bool] = None
    bot_user_id: Optional[str] = None
    dm_allowlist: Optional[List[str]] = None
    space_id: Optional[str] = None
    bridge_enabled: Optional[bool] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    emoji: str
    enabled: bool
    model: str
    mcp_servers: List[str]
    bot_user_id: Optional[str] = None
    space_id: Optional[str] = None
    bridge_enabled: bool = False
    bridge_running: bool = False
    state: str
    uptime_s: int
    current_task: Optional[str] = None
    pending_assignments: int = 0
    recent_tasks: int = 0
    description: str = ""


class AgentDetailResponse(AgentResponse):
    soul: str
    created_at: str
    updated_at: str
    dm_allowlist: List[str] = Field(default_factory=list)


class MemoryResponse(BaseModel):
    content: str


class MemoryUpdateRequest(BaseModel):
    content: str


class TaskEventResponse(BaseModel):
    timestamp: float
    kind: str
    payload: Dict[str, object] = Field(default_factory=dict)


class TaskSummaryResponse(BaseModel):
    id: str
    prompt: str
    priority: str
    timeout_s: float
    mcp_servers: List[str] = Field(default_factory=list)
    session_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    agent_id: Optional[str] = None
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class TaskDetailResponse(TaskSummaryResponse):
    events: List[TaskEventResponse] = Field(default_factory=list)


class TaskListResponse(BaseModel):
    tasks: List[TaskSummaryResponse] = Field(default_factory=list)


class SubAgentRequest(BaseModel):
    prompt: str = Field(min_length=1)
    timeout_s: Optional[float] = Field(default=None, gt=0)
    mcp_servers: Optional[List[str]] = None
    parent_task_id: Optional[str] = None


class SubAgentResponse(BaseModel):
    session_name: str
    agent_id: str
    prompt: str
    mcp_server_names: List[str] = Field(default_factory=list)
    parent_task_id: Optional[str] = None
    status: str
    created_at: float
    started_at: float
    completed_at: Optional[float] = None
    timeout_s: float
    output: Optional[str] = None
    error: Optional[str] = None


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1)
    agent_id: Optional[str] = None
    memory_types: List[str] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=200)


class MemorySearchResult(BaseModel):
    agent_id: str
    memory_type: str
    source: str
    snippet: str
    line_number: int
    timestamp: Optional[str] = None


class MemorySearchResponse(BaseModel):
    results: List[MemorySearchResult] = Field(default_factory=list)


class SessionListResponse(BaseModel):
    sessions: List[str]


class KnowledgeListResponse(BaseModel):
    items: List[str]


class LinkBotRequest(BaseModel):
    bot_user_id: str = Field(min_length=1)


class TestAgentRequest(BaseModel):
    text: str = Field(default="ping", min_length=1)


class AgentBridgeStatus(BaseModel):
    agent_id: str
    space_id: Optional[str] = None
    space_name: Optional[str] = None
    bridge_enabled: bool = False
    is_running: bool = False


class SetupStatus(BaseModel):
    credentials_ok: bool
    auth_ok: bool
    space_configured: bool
    bridge_enabled: bool
    bridge_running: bool
    agents_ready: bool
    completed: bool
    agent_bridges: List[AgentBridgeStatus] = Field(default_factory=list)
    space_id: Optional[str] = None
    space_name: Optional[str] = None
    email_enabled: bool = False
    email_base_address: str = ""
    email_poll_interval_s: float = 30.0
    debug_mode: bool = False


class CredentialsUploadRequest(BaseModel):
    credentials: Dict[str, object]


class CompleteAuthRequest(BaseModel):
    code: str = Field(min_length=1)


class SpaceConfigRequest(BaseModel):
    space_id: str = Field(min_length=1)
    space_name: Optional[str] = None


class SleepAgentRequest(BaseModel):
    duration_s: float = Field(gt=0, le=86400, description="Sleep duration in seconds (max 24h)")


# --- Journal models ---


class JournalEntryResponse(BaseModel):
    id: str
    timestamp: str
    content: str
    salience: str
    tags: List[str] = Field(default_factory=list)
    source_session: str = ""
    associations: List[str] = Field(default_factory=list)


class JournalEntryCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    salience: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_session: str = ""
    associations: List[str] = Field(default_factory=list)


class JournalQueryRequest(BaseModel):
    salience_min: Optional[str] = None
    tags: Optional[List[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=500)


class JournalQueryResponse(BaseModel):
    entries: List[JournalEntryResponse] = Field(default_factory=list)


class AssociationResponse(BaseModel):
    source_id: str
    target_id: str
    relation_type: str = "related"
    weight: float = 1.0


class AssociationListResponse(BaseModel):
    associations: List[AssociationResponse] = Field(default_factory=list)
