"""Named agent runtime registry."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from g3lobster.agents.persona import AgentPersona, agent_dir, list_personas, load_persona
from g3lobster.agents.subagent_registry import RunStatus, SubagentRegistry, SubagentRun
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.pool.health import HealthInspector
from g3lobster.tasks.types import Task, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class RegisteredAgent:
    """Runtime handle wrapping a Gemini agent with assignment serialization."""

    persona: AgentPersona
    agent: object
    memory_manager: MemoryManager
    context_builder: ContextBuilder
    _assign_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pending_assignments: int = 0

    @property
    def id(self) -> str:
        return self.persona.id

    @property
    def state(self):
        return getattr(self.agent, "state", None)

    @property
    def started_at(self) -> float:
        return float(getattr(self.agent, "started_at", time.time()))

    @property
    def current_task(self):
        return getattr(self.agent, "current_task", None)

    @property
    def mcp_servers(self) -> List[str]:
        return list(getattr(self.agent, "mcp_servers", []))

    @property
    def pending_assignments(self) -> int:
        # Includes the currently running assignment, so expose queued only.
        return max(0, self._pending_assignments - 1)

    async def assign(self, task):
        self._pending_assignments += 1
        try:
            async with self._assign_lock:
                return await self.agent.assign(task)
        finally:
            self._pending_assignments -= 1


class AgentRegistry:
    """Manages named agents and their per-agent runtime dependencies."""

    def __init__(
        self,
        data_dir: str,
        context_messages: int,
        health_check_interval_s: int,
        stuck_timeout_s: int,
        agent_factory: Callable[[AgentPersona, MemoryManager, ContextBuilder], object],
        compact_threshold: int = 40,
        compact_keep_ratio: float = 0.25,
        compact_chunk_size: int = 10,
        procedure_min_frequency: int = 3,
        memory_max_sections: int = 50,
        gemini_command: str = "gemini",
        gemini_args: Optional[List[str]] = None,
        gemini_timeout_s: float = 45.0,
        gemini_cwd: Optional[str] = None,
        global_memory_manager: Optional[GlobalMemoryManager] = None,
        # Legacy parameter; ignored.
        summarize_threshold: int = 20,
    ):
        self.data_dir = data_dir
        self.compact_threshold = compact_threshold
        self.compact_keep_ratio = compact_keep_ratio
        self.compact_chunk_size = compact_chunk_size
        self.procedure_min_frequency = procedure_min_frequency
        self.memory_max_sections = memory_max_sections
        self.gemini_command = gemini_command
        self.gemini_args = list(gemini_args) if gemini_args is not None else ["-y"]
        self.gemini_timeout_s = gemini_timeout_s
        self.gemini_cwd = gemini_cwd
        self.context_messages = context_messages
        self.health_check_interval_s = health_check_interval_s
        self.stuck_timeout_s = stuck_timeout_s
        self.global_memory_manager = global_memory_manager
        self.agent_factory = agent_factory

        self.health = HealthInspector()
        self.subagent_registry = SubagentRegistry(Path(data_dir))
        self._agents: Dict[str, RegisteredAgent] = {}

        self._health_task: Optional[asyncio.Task] = None
        self._stopping = False

    async def start_all(self) -> None:
        self._stopping = False
        for persona in list_personas(self.data_dir):
            if persona.enabled:
                await self.start_agent(persona.id)

        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop(), name="g3lobster-agent-health")

    async def stop_all(self) -> None:
        self._stopping = True
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None

        for agent_id in list(self._agents.keys()):
            await self.stop_agent(agent_id)

    async def start_agent(self, agent_id: str) -> bool:
        if agent_id in self._agents:
            return True

        persona = load_persona(self.data_dir, agent_id)
        if not persona:
            return False

        runtime_dir = str(agent_dir(self.data_dir, agent_id))
        memory = MemoryManager(
            data_dir=runtime_dir,
            compact_threshold=self.compact_threshold,
            compact_keep_ratio=self.compact_keep_ratio,
            compact_chunk_size=self.compact_chunk_size,
            procedure_min_frequency=self.procedure_min_frequency,
            memory_max_sections=self.memory_max_sections,
            gemini_command=self.gemini_command,
            gemini_args=self.gemini_args,
            gemini_timeout_s=self.gemini_timeout_s,
            gemini_cwd=self.gemini_cwd,
        )
        current_agent_id = agent_id

        def _agent_list_provider():
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "emoji": p.emoji,
                    "description": (p.soul.split("\n")[0].strip() if p.soul else ""),
                }
                for p in self.list_enabled_personas()
                if p.id != current_agent_id
            ]

        context = ContextBuilder(
            memory_manager=memory,
            message_limit=self.context_messages,
            system_preamble=persona.soul,
            global_memory_manager=self.global_memory_manager,
            agent_list_provider=_agent_list_provider,
        )
        agent = self.agent_factory(persona, memory, context)
        await agent.start(mcp_servers=persona.mcp_servers)

        self._agents[agent_id] = RegisteredAgent(
            persona=persona,
            agent=agent,
            memory_manager=memory,
            context_builder=context,
        )
        return True

    async def stop_agent(self, agent_id: str) -> bool:
        runtime = self._agents.pop(agent_id, None)
        if not runtime:
            return False
        await runtime.agent.stop()
        return True

    async def restart_agent(self, agent_id: str) -> bool:
        if agent_id in self._agents:
            await self.stop_agent(agent_id)
        return await self.start_agent(agent_id)

    def get_agent(self, agent_id: str) -> Optional[RegisteredAgent]:
        return self._agents.get(agent_id)

    def list_enabled_personas(self) -> list:
        """Return personas for all currently running agents (no disk I/O)."""
        return [rt.persona for rt in self._agents.values() if rt.persona.enabled]

    def load_persona(self, agent_id: str) -> Optional[AgentPersona]:
        return load_persona(self.data_dir, agent_id)

    async def delegate_task(
        self,
        parent_agent_id: str,
        child_agent_id: str,
        task_prompt: str,
        parent_session_id: str,
        timeout_s: float = 300.0,
    ) -> SubagentRun:
        """Parent agent delegates a task to child agent."""
        if parent_agent_id == child_agent_id:
            raise ValueError("Circular delegation is not allowed: agent cannot delegate to itself")

        run = self.subagent_registry.register_run(
            parent_agent_id=parent_agent_id,
            child_agent_id=child_agent_id,
            task=task_prompt,
            parent_session_id=parent_session_id,
            timeout_s=timeout_s,
        )

        # Ensure child agent is running
        child = self.get_agent(child_agent_id)
        if not child:
            started = await self.start_agent(child_agent_id)
            if not started:
                self.subagent_registry.fail_run(
                    run.run_id, f"Failed to start agent {child_agent_id}"
                )
                return self.subagent_registry.get_run(run.run_id)
            child = self.get_agent(child_agent_id)

        # Mark as running
        run.status = RunStatus.RUNNING
        self.subagent_registry._save_to_disk()

        # Assign task to child agent
        child_task = Task(prompt=task_prompt, session_id=run.session_id)
        result_task = await child.assign(child_task)

        # Record result
        if result_task.status == TaskStatus.COMPLETED and result_task.result:
            self.subagent_registry.complete_run(run.run_id, result_task.result)
        else:
            self.subagent_registry.fail_run(
                run.run_id, result_task.error or "Unknown failure"
            )

        return self.subagent_registry.get_run(run.run_id)

    async def status(self) -> Dict[str, object]:
        now = time.time()
        items: List[Dict[str, object]] = []

        for persona in list_personas(self.data_dir):
            runtime = self._agents.get(persona.id)
            if runtime:
                raw_state = runtime.state
                state = raw_state.value if hasattr(raw_state, "value") else str(raw_state)
                item = {
                    "id": persona.id,
                    "name": persona.name,
                    "emoji": persona.emoji,
                    "enabled": persona.enabled,
                    "bot_user_id": persona.bot_user_id,
                    "model": persona.model,
                    "mcp_servers": runtime.mcp_servers or list(persona.mcp_servers),
                    "state": state,
                    "uptime_s": int(now - runtime.started_at),
                    "current_task": runtime.current_task.id if runtime.current_task else None,
                    "pending_assignments": runtime.pending_assignments,
                }
            else:
                item = {
                    "id": persona.id,
                    "name": persona.name,
                    "emoji": persona.emoji,
                    "enabled": persona.enabled,
                    "bot_user_id": persona.bot_user_id,
                    "model": persona.model,
                    "mcp_servers": list(persona.mcp_servers),
                    "state": "stopped",
                    "uptime_s": 0,
                    "current_task": None,
                    "pending_assignments": 0,
                }
            items.append(item)

        return {"agents": items}

    async def _health_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.health_check_interval_s)

            active = list(self._agents.values())
            issues = self.health.inspect([item.agent for item in active], self.stuck_timeout_s)
            for issue in issues:
                if issue.issue not in {"dead", "stuck"}:
                    continue
                if issue.agent_id not in self._agents:
                    continue
                await self.restart_agent(issue.agent_id)

            # Sweep timed-out delegation runs
            timed_out = self.subagent_registry.check_timeouts()
            for run in timed_out:
                logger.warning(
                    "Delegation run %s timed out (%s -> %s)",
                    run.run_id,
                    run.parent_agent_id,
                    run.child_agent_id,
                )
