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


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    emoji: Optional[str] = None
    soul: Optional[str] = None
    model: Optional[str] = None
    mcp_servers: Optional[List[str]] = None
    enabled: Optional[bool] = None
    bot_user_id: Optional[str] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    emoji: str
    enabled: bool
    model: str
    mcp_servers: List[str]
    bot_user_id: Optional[str] = None
    state: str
    uptime_s: int
    current_task: Optional[str] = None
    pending_assignments: int = 0


class AgentDetailResponse(AgentResponse):
    soul: str
    created_at: str
    updated_at: str


class MemoryResponse(BaseModel):
    content: str


class MemoryUpdateRequest(BaseModel):
    content: str


class SessionListResponse(BaseModel):
    sessions: List[str]


class KnowledgeListResponse(BaseModel):
    items: List[str]


class LinkBotRequest(BaseModel):
    bot_user_id: str = Field(min_length=1)


class TestAgentRequest(BaseModel):
    text: str = Field(default="ping", min_length=1)


class SetupStatus(BaseModel):
    credentials_ok: bool
    auth_ok: bool
    space_configured: bool
    bridge_enabled: bool
    bridge_running: bool
    agents_ready: bool
    completed: bool
    space_id: Optional[str] = None
    space_name: Optional[str] = None


class CredentialsUploadRequest(BaseModel):
    credentials: Dict[str, object]


class CompleteAuthRequest(BaseModel):
    code: str = Field(min_length=1)


class SpaceConfigRequest(BaseModel):
    space_id: str = Field(min_length=1)
    space_name: Optional[str] = None
