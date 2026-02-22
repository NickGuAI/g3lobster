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
from g3lobster.agents.subagent_registry import SubagentRegistry, SubagentRun
from g3lobster.memory.context import ContextBuilder, summarize_agent_soul
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
        subagent_registry: Optional[SubagentRegistry] = None,
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
        self.subagent_registry = subagent_registry or SubagentRegistry(Path(self.data_dir))
        self.agent_factory = agent_factory

        self.health = HealthInspector()
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
        context = ContextBuilder(
            memory_manager=memory,
            message_limit=self.context_messages,
            system_preamble=persona.soul,
            global_memory_manager=self.global_memory_manager,
            agent_id=persona.id,
            delegation_agents_provider=lambda current_agent_id=persona.id: self._delegation_agents(current_agent_id),
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

    def _delegation_agents(self, current_agent_id: str) -> List[tuple[str, str]]:
        peers: List[tuple[str, str]] = []
        for persona in list_personas(self.data_dir):
            if not persona.enabled:
                continue
            if persona.id == current_agent_id:
                continue
            peers.append((persona.id, summarize_agent_soul(persona.soul)))
        return peers

    async def delegate_task(
        self,
        parent_agent_id: str,
        child_agent_id: str,
        task_prompt: str,
        parent_session_id: str,
        timeout_s: float = 300.0,
    ) -> SubagentRun:
        """Delegate a task from one agent to another and wait for the result."""
        run = self.subagent_registry.register_run(
            parent_agent_id=parent_agent_id,
            child_agent_id=child_agent_id,
            task=task_prompt,
            parent_session_id=parent_session_id,
            timeout_s=timeout_s,
        )

        child = self.get_agent(child_agent_id)
        if not child:
            started = await self.start_agent(child_agent_id)
            if not started:
                failed = self.subagent_registry.fail_run(run.run_id, f"Failed to start agent {child_agent_id}")
                return failed or run
            child = self.get_agent(child_agent_id)
            if not child:
                failed = self.subagent_registry.fail_run(
                    run.run_id,
                    f"Agent {child_agent_id} started but runtime was unavailable",
                )
                return failed or run

        self.subagent_registry.mark_running(run.run_id)

        delegated_task = Task(
            prompt=str(task_prompt),
            session_id=run.session_id,
            timeout_s=max(1.0, float(timeout_s)),
        )
        try:
            result_task = await child.assign(delegated_task)
        except Exception as exc:
            failed = self.subagent_registry.fail_run(run.run_id, str(exc))
            return failed or run

        if result_task.status == TaskStatus.COMPLETED and result_task.result is not None:
            completed = self.subagent_registry.complete_run(run.run_id, result_task.result)
            return completed or run

        failed = self.subagent_registry.fail_run(
            run.run_id,
            result_task.error or "Delegated task failed",
        )
        return failed or run

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

            timed_out = self.subagent_registry.check_timeouts()
            for run in timed_out:
                logger.warning(
                    "Delegation run %s timed out (%s -> %s)",
                    run.run_id,
                    run.parent_agent_id,
                    run.child_agent_id,
                )
