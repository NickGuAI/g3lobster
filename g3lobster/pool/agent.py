"""Gemini agent implementation for pooled execution."""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from g3lobster.cli.parser import clean_text, strip_reasoning
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.manager import MemoryManager
from g3lobster.mcp.manager import MCPManager
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus


class GeminiAgent:
    """Represents a single long-lived Gemini CLI worker."""

    def __init__(
        self,
        agent_id: str,
        process_factory: Callable[[], object],
        mcp_manager: MCPManager,
        memory_manager: MemoryManager,
        context_builder: ContextBuilder,
        default_mcp_servers: Optional[List[str]] = None,
    ):
        self.id = agent_id
        self.state = AgentState.STARTING
        self._process_factory = process_factory
        self.process = None
        self.mcp_manager = mcp_manager
        self.memory_manager = memory_manager
        self.context_builder = context_builder
        self.default_mcp_servers = default_mcp_servers or ["*"]
        self.mcp_servers: List[str] = list(self.default_mcp_servers)
        self.current_task: Optional[Task] = None
        self.started_at = time.time()
        self.busy_since: Optional[float] = None

    async def start(self, mcp_servers: Optional[List[str]] = None) -> None:
        selected_servers = self.mcp_manager.resolve_server_names(
            selected_mcps=mcp_servers or self.default_mcp_servers
        )
        self.mcp_servers = list(selected_servers)

        self.process = self._process_factory()
        await self.process.spawn(mcp_server_names=self.mcp_servers)
        self.state = AgentState.IDLE

    def is_alive(self) -> bool:
        return bool(self.process and self.process.is_alive())

    async def stop(self) -> None:
        if self.process:
            await self.process.kill()
        self.state = AgentState.STOPPED

    async def assign(self, task: Task) -> Task:
        if self.state not in {AgentState.IDLE, AgentState.BUSY}:
            raise RuntimeError(f"Agent {self.id} is not ready")

        self.current_task = task
        self.busy_since = time.time()
        self.state = AgentState.BUSY

        task.status = TaskStatus.RUNNING
        task.agent_id = self.id
        task.started_at = time.time()
        task.add_event("started", {"agent_id": self.id})

        try:
            prompt = self.context_builder.build(task.session_id, task.prompt)
            self.memory_manager.append_message(task.session_id, "user", task.prompt, {"task_id": task.id})
            raw_output = await self.process.ask(prompt, timeout=task.timeout_s)
            parsed = strip_reasoning(clean_text(raw_output))
            task.result = parsed
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.add_event("completed", {"chars": len(parsed or "")})
            self.memory_manager.append_message(task.session_id, "assistant", parsed, {"task_id": task.id})
        except Exception as exc:  # pragma: no cover - defensive path
            task.error = str(exc)
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.add_event("failed", {"error": task.error})
        finally:
            self.current_task = None
            self.busy_since = None
            if self.is_alive():
                self.state = AgentState.IDLE
            else:
                self.state = AgentState.DEAD

        return task
