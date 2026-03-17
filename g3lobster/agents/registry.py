"""Named agent runtime registry."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from g3lobster.agents.persona import AgentPersona, agent_dir, list_personas, load_persona
from g3lobster.agents.subagent_registry import SubagentRegistry, SubagentRun
from g3lobster.alerts import AlertManager, make_event
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.handoff import HandoffBuilder
from g3lobster.memory.manager import MemoryManager
from g3lobster.pool.health import HealthInspector
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus, TaskStore
from g3lobster.tmux.spawner import SubAgentRunInfo, SubAgentSpawner

logger = logging.getLogger(__name__)


@dataclass
class RegisteredAgent:
    """Runtime handle wrapping a Gemini agent with assignment serialization."""

    persona: AgentPersona
    agent: object
    memory_manager: MemoryManager
    context_builder: ContextBuilder
    task_store: Optional[TaskStore] = None
    max_queue_depth: int = 5
    _assign_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _queue: "asyncio.Queue[Tuple[Any, asyncio.Future, Optional[Callable[[], None]], Optional[asyncio.Queue]]]" = field(
        default_factory=asyncio.Queue
    )
    _queue_worker: Optional[asyncio.Task] = None

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
        return self._queue.qsize()

    @property
    def queue_load(self) -> int:
        current = 1 if self.current_task is not None else 0
        return self.pending_assignments + current

    async def assign(self, task, on_started: Optional[Callable[[], None]] = None):
        if self.queue_load >= self.max_queue_depth:
            raise RuntimeError(f"Agent {self.id} queue is full")

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put((task, future, on_started, None))
        self._ensure_queue_worker()
        return await future

    async def assign_stream(self, task):
        """Assign a task and yield streaming events using the same FIFO queue as assign()."""
        if self.queue_load >= self.max_queue_depth:
            raise RuntimeError(f"Agent {self.id} queue is full")

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        stream_queue: asyncio.Queue = asyncio.Queue()
        await self._queue.put((task, future, None, stream_queue))
        self._ensure_queue_worker()

        while True:
            kind, payload = await stream_queue.get()
            if kind == "event":
                yield payload
                continue
            if kind == "error":
                if isinstance(payload, BaseException):
                    raise payload
                raise RuntimeError(str(payload))
            if kind == "done":
                break

        if not future.done():
            await future

    async def stop(self) -> None:
        if self._queue_worker and not self._queue_worker.done():
            self._queue_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._queue_worker
        await self.agent.stop()

    def _ensure_queue_worker(self) -> None:
        if self._queue_worker and not self._queue_worker.done():
            return
        self._queue_worker = asyncio.create_task(self._queue_loop(), name=f"g3lobster-agent-queue-{self.id}")

    def _record_task(self, task) -> None:
        """Persist a terminal task result to the task store if available."""
        if self.task_store and hasattr(task, "status") and task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }:
            self.task_store.add(task)

    async def _queue_loop(self) -> None:
        while True:
            task, future, on_started, stream_queue = await self._queue.get()
            try:
                async with self._assign_lock:
                    if callable(on_started):
                        on_started()
                    if stream_queue is None:
                        result = await self.agent.assign(task)
                        self._record_task(result)
                        if not future.done():
                            future.set_result(result)
                    else:
                        async for event in self.agent.assign_stream(task):
                            await stream_queue.put(("event", event))
                        self._record_task(task)
                        if not future.done():
                            future.set_result(task)
            except asyncio.CancelledError:
                if stream_queue is None:
                    if not future.done():
                        future.set_exception(asyncio.CancelledError())
                else:
                    await stream_queue.put(("error", asyncio.CancelledError()))
                    if not future.done():
                        future.set_result(task)
                raise
            except Exception as exc:
                if stream_queue is None:
                    if not future.done():
                        future.set_exception(exc)
                else:
                    await stream_queue.put(("error", exc))
                    if not future.done():
                        future.set_result(task)
            finally:
                if stream_queue is not None:
                    await stream_queue.put(("done", None))
                self._queue.task_done()


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
        task_store_size: int = 100,
        subagent_max_concurrent: int = 3,
        subagent_timeout_s: float = 300.0,
        global_memory_manager: Optional[GlobalMemoryManager] = None,
        alert_manager: Optional[AlertManager] = None,
        chat_bridge: Optional[object] = None,
        queue_depth_limit: int = 5,
        event_bus: Optional[object] = None,
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
        self.alert_manager = alert_manager
        self.chat_bridge = chat_bridge
        self.queue_depth_limit = max(1, int(queue_depth_limit))
        self.event_bus = event_bus
        self._chat_bridge_was_running = False
        self.agent_factory = agent_factory
        self.control_plane = None

        self.health = HealthInspector()
        self.task_store = TaskStore(max_tasks_per_agent=task_store_size)
        self.subagent_registry = SubagentRegistry(Path(data_dir))
        self.tmux_subagent_spawner = SubAgentSpawner(
            command=self.gemini_command,
            args=self.gemini_args,
            cwd=self.gemini_cwd,
            max_concurrent_per_agent=subagent_max_concurrent,
            default_timeout_s=subagent_timeout_s,
        )
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

        # Resolve space context for agents bound to a specific space.
        agent_space_id = persona.space_id
        agent_space_name: Optional[str] = None
        if agent_space_id and self.chat_bridge is not None:
            agent_space_name = getattr(self.chat_bridge, "space_name", None)
        # Apply per-space soul override when the agent is bound to a space.
        effective_soul = persona.get_soul_for_space(agent_space_id)

        context = ContextBuilder(
            memory_manager=memory,
            message_limit=self.context_messages,
            system_preamble=effective_soul,
            global_memory_manager=self.global_memory_manager,
            agent_list_provider=_agent_list_provider,
            space_id=agent_space_id,
            space_name=agent_space_name,
        )
        agent = self.agent_factory(persona, memory, context)
        setattr(agent, "task_store", self.task_store)
        setattr(agent, "subagent_spawner", self.tmux_subagent_spawner)

        async def _heartbeat_review_provider() -> Optional[dict]:
            runtime = self._agents.get(agent_id)
            active_persona = runtime.persona if runtime else persona
            active_memory = runtime.memory_manager if runtime else memory
            active_agent = runtime.agent if runtime else agent
            return self._build_heartbeat_event(
                agent_id=agent_id,
                persona=active_persona,
                memory_manager=active_memory,
                agent=active_agent,
            )

        if hasattr(agent, "set_heartbeat_review_provider"):
            agent.set_heartbeat_review_provider(_heartbeat_review_provider)
        else:
            setattr(agent, "heartbeat_review_provider", _heartbeat_review_provider)

        if hasattr(agent, "set_heartbeat_event_publisher"):
            agent.set_heartbeat_event_publisher(self._publish_event_bus)
        else:
            setattr(agent, "heartbeat_event_publisher", self._publish_event_bus)

        if hasattr(agent, "configure_heartbeat"):
            agent.configure_heartbeat(
                enabled=persona.heartbeat_enabled,
                interval_s=persona.heartbeat_interval_s,
            )
        else:
            setattr(agent, "heartbeat_enabled", bool(persona.heartbeat_enabled))
            setattr(agent, "heartbeat_interval_s", float(persona.heartbeat_interval_s))

        await agent.start(mcp_servers=persona.mcp_servers)

        self._agents[agent_id] = RegisteredAgent(
            persona=persona,
            agent=agent,
            memory_manager=memory,
            context_builder=context,
            task_store=self.task_store,
            max_queue_depth=self.queue_depth_limit,
        )
        return True

    async def stop_agent(self, agent_id: str) -> bool:
        runtime = self._agents.pop(agent_id, None)
        if not runtime:
            return False
        await self.tmux_subagent_spawner.kill_agent_runs(agent_id)
        await runtime.stop()
        return True

    async def restart_agent(self, agent_id: str) -> bool:
        if agent_id in self._agents:
            await self.stop_agent(agent_id)
        return await self.start_agent(agent_id)

    async def sleep_agent(self, agent_id: str, duration_s: float) -> bool:
        """Put an agent to sleep for a specified duration.

        The agent transitions to SLEEPING state and will automatically
        wake up (restart) after the duration elapses.
        """
        runtime = self.get_agent(agent_id)
        if not runtime:
            return False

        agent = runtime.agent
        if hasattr(agent, 'state'):
            agent.state = AgentState.SLEEPING

        # Schedule wake-up
        asyncio.get_event_loop().call_later(
            duration_s,
            lambda: asyncio.ensure_future(self._wake_agent(agent_id)),
        )
        return True

    async def _wake_agent(self, agent_id: str) -> None:
        """Wake a sleeping agent by restarting it."""
        runtime = self.get_agent(agent_id)
        if not runtime:
            return

        agent_state = getattr(runtime.agent, 'state', None)
        if agent_state != AgentState.SLEEPING:
            return  # Agent was manually restarted or stopped — skip wake

        await self.restart_agent(agent_id)

        if self.alert_manager:
            from g3lobster.alerts import make_event
            await self.alert_manager.send(make_event(
                event_type="agent_woke",
                agent_id=agent_id,
                detail=f"Agent {agent_id} woke up from sleep",
            ))

    def get_agent(self, agent_id: str) -> Optional[RegisteredAgent]:
        return self._agents.get(agent_id)

    def active_agents(self) -> List[RegisteredAgent]:
        return list(self._agents.values())

    def list_enabled_personas(self) -> list:
        """Return personas for all enabled agents (includes stopped agents)."""
        return [p for p in list_personas(self.data_dir) if p.enabled]

    def load_persona(self, agent_id: str) -> Optional[AgentPersona]:
        return load_persona(self.data_dir, agent_id)

    def list_tasks(self, agent_id: str, limit: int = 20) -> List[Task]:
        capped_limit = max(1, int(limit))
        runtime = self.get_agent(agent_id)
        items = self.task_store.list(agent_id, limit=capped_limit)
        current = runtime.current_task if runtime else None
        if current and all(existing.id != current.id for existing in items):
            items = [current, *items]
        return items[:capped_limit]

    def get_task(self, agent_id: str, task_id: str) -> Optional[Task]:
        runtime = self.get_agent(agent_id)
        if runtime and runtime.current_task and runtime.current_task.id == task_id:
            return runtime.current_task
        return self.task_store.get(agent_id, task_id)

    async def cancel_task(self, agent_id: str, task_id: str) -> Optional[Task]:
        runtime = self.get_agent(agent_id)
        if not runtime:
            return self.task_store.get(agent_id, task_id)

        current = runtime.current_task
        if not current or current.id != task_id:
            return self.task_store.get(agent_id, task_id)

        if current.status != TaskStatus.RUNNING:
            return current

        agent = runtime.agent
        if hasattr(agent, "cancel_task"):
            canceled = await agent.cancel_task(task_id)
            if canceled:
                self.task_store.add(canceled)
            return canceled

        process = getattr(agent, "process", None)
        if process and hasattr(process, "kill"):
            await process.kill()
        current.status = TaskStatus.CANCELED
        current.completed_at = current.completed_at or time.time()
        current.error = current.error or "Canceled by API request"
        current.add_event("canceled", {"reason": current.error})
        self.task_store.add(current)
        return current

    async def spawn_subagent(
        self,
        agent_id: str,
        prompt: str,
        timeout_s: Optional[float] = None,
        mcp_servers: Optional[List[str]] = None,
        parent_task_id: Optional[str] = None,
    ) -> SubAgentRunInfo:
        runtime = self.get_agent(agent_id)
        if not runtime:
            raise ValueError(f"Agent '{agent_id}' is not running")
        selected_servers = list(mcp_servers or runtime.mcp_servers or ["*"])
        return await self.tmux_subagent_spawner.spawn(
            agent_id=agent_id,
            prompt=prompt,
            timeout_s=timeout_s,
            mcp_server_names=selected_servers,
            parent_task_id=parent_task_id,
        )

    async def list_subagents(self, agent_id: str, active_only: bool = True) -> List[SubAgentRunInfo]:
        return await self.tmux_subagent_spawner.list_runs(agent_id=agent_id, active_only=active_only)

    async def kill_subagent(self, agent_id: str, session_name: str) -> Optional[SubAgentRunInfo]:
        return await self.tmux_subagent_spawner.kill(agent_id=agent_id, session_name=session_name)

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

        # Mark as running (records started_at and persists)
        self.subagent_registry.mark_running(run.run_id)

        # Enrich task prompt with parent's context
        enriched_prompt = self._build_handoff(parent_agent_id, task_prompt)

        # Assign task to child agent
        child_task = Task(prompt=enriched_prompt, session_id=run.session_id, timeout_s=timeout_s)
        result_task = await child.assign(child_task)

        # Record result
        if result_task.status == TaskStatus.COMPLETED and result_task.result:
            self.subagent_registry.complete_run(run.run_id, result_task.result)
        else:
            self.subagent_registry.fail_run(
                run.run_id, result_task.error or "Unknown failure"
            )

        return self.subagent_registry.get_run(run.run_id)

    async def delegate_task_stream(
        self,
        parent_agent_id: str,
        child_agent_id: str,
        task_prompt: str,
        parent_session_id: str,
        timeout_s: float = 300.0,
    ):
        """Delegate a task with streaming output. Yields StreamEvent objects."""
        if parent_agent_id == child_agent_id:
            raise ValueError("Circular delegation is not allowed")

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
                self.subagent_registry.fail_run(
                    run.run_id, f"Failed to start agent {child_agent_id}"
                )
                return
            child = self.get_agent(child_agent_id)

        self.subagent_registry.mark_running(run.run_id)

        # Enrich task prompt with parent's context
        enriched_prompt = self._build_handoff(parent_agent_id, task_prompt)

        child_task = Task(prompt=enriched_prompt, session_id=run.session_id, timeout_s=timeout_s)

        collected_text = []
        try:
            async for event in child.assign_stream(child_task):
                yield event
                if hasattr(event, 'text') and event.text:
                    collected_text.append(event.text)

            if child_task.status == TaskStatus.COMPLETED and child_task.result:
                self.subagent_registry.complete_run(run.run_id, child_task.result)
            else:
                self.subagent_registry.fail_run(
                    run.run_id, child_task.error or "Unknown failure"
                )
        except Exception as exc:
            self.subagent_registry.fail_run(run.run_id, str(exc))
            raise

    def _build_handoff(self, parent_agent_id: str, task_prompt: str) -> str:
        """Enrich a delegation prompt with the parent agent's context."""
        parent = self._agents.get(parent_agent_id)
        if not parent:
            return task_prompt
        builder = HandoffBuilder()
        return builder.build(
            task_prompt=task_prompt,
            parent_memory=parent.memory_manager,
            global_memory=self.global_memory_manager,
            parent_persona_name=parent.persona.name,
        )

    def _resolve_event_bus(self) -> Optional[object]:
        if self.event_bus is not None:
            return self.event_bus
        try:
            from g3lobster.api.event_bus import EventBus
        except Exception:  # pragma: no cover - defensive import guard
            return None
        latest = EventBus.latest()
        if latest is not None:
            self.event_bus = latest
        return latest

    def _publish_event_bus(self, agent_id: str, event: Dict[str, object]) -> None:
        bus = self._resolve_event_bus()
        if bus is None or not hasattr(bus, "publish"):
            return
        bus.publish(agent_id, event)

    def _control_plane_tasks_for_agent(self, agent_id: str) -> List[object]:
        if self.control_plane is None or not hasattr(self.control_plane, "task_registry"):
            return []
        task_registry = getattr(self.control_plane, "task_registry", None)
        if task_registry is None or not hasattr(task_registry, "list"):
            return []
        try:
            tasks = task_registry.list(limit=200)
        except Exception:  # pragma: no cover - defensive path
            logger.debug("Failed to read control-plane tasks for agent %s", agent_id, exc_info=True)
            return []
        return [item for item in tasks if getattr(item, "agent_id", None) == agent_id]

    @staticmethod
    def _goal_sources(persona: AgentPersona, memory_manager: MemoryManager) -> List[str]:
        sources: List[str] = []
        if persona.soul:
            sources.append(persona.soul)
        try:
            procedures = memory_manager.read_procedures()
        except Exception:  # pragma: no cover - defensive path
            procedures = ""
        if procedures:
            sources.append(procedures)
        return sources

    def _build_heartbeat_event(
        self,
        *,
        agent_id: str,
        persona: AgentPersona,
        memory_manager: MemoryManager,
        agent: object,
    ) -> Dict[str, object]:
        tasks = self.task_store.list(agent_id, limit=120)
        current = getattr(agent, "current_task", None)
        if current is not None and all(existing.id != getattr(current, "id", None) for existing in tasks):
            tasks = [current, *tasks]

        review = self.health.build_heartbeat_review(
            agent_id=agent_id,
            tasks=tasks,
            goals=self._goal_sources(persona, memory_manager),
            control_plane_tasks=self._control_plane_tasks_for_agent(agent_id),
        )
        return review.as_event(agent_id)

    @staticmethod
    def _soul_summary(soul: str) -> str:
        """Return the first non-empty line of the SOUL.md as a brief description."""
        for line in (soul or "").splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped
        return ""

    async def status(self) -> Dict[str, object]:
        now = time.time()
        items: List[Dict[str, object]] = []

        for persona in list_personas(self.data_dir):
            description = self._soul_summary(persona.soul)
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
                    "heartbeat_enabled": persona.heartbeat_enabled,
                    "heartbeat_interval_s": persona.heartbeat_interval_s,
                    "state": state,
                    "uptime_s": int(now - runtime.started_at),
                    "current_task": runtime.current_task.id if runtime.current_task else None,
                    "pending_assignments": runtime.pending_assignments,
                    "recent_tasks": len(self.task_store.list(persona.id)),
                    "description": description,
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
                    "heartbeat_enabled": persona.heartbeat_enabled,
                    "heartbeat_interval_s": persona.heartbeat_interval_s,
                    "state": "stopped",
                    "uptime_s": 0,
                    "current_task": None,
                    "pending_assignments": 0,
                    "recent_tasks": len(self.task_store.list(persona.id)),
                    "description": description,
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
                # Skip sleeping agents — they are intentionally inactive
                sleeping_runtime = self._agents.get(issue.agent_id)
                if sleeping_runtime and getattr(sleeping_runtime.agent, 'state', None) == AgentState.SLEEPING:
                    continue
                if self.alert_manager:
                    await self.alert_manager.send(make_event(
                        event_type=f"agent_{issue.issue}",
                        agent_id=issue.agent_id,
                        detail=f"Agent {issue.agent_id} detected as {issue.issue}, restarting",
                    ))
                restarted = await self.restart_agent(issue.agent_id)
                if restarted and self.alert_manager:
                    await self.alert_manager.send(make_event(
                        event_type="agent_restarted",
                        agent_id=issue.agent_id,
                        detail=f"Agent {issue.agent_id} successfully restarted",
                    ))

            # Sweep timed-out delegation runs
            timed_out = self.subagent_registry.check_timeouts()
            for run in timed_out:
                logger.warning(
                    "Delegation run %s timed out (%s -> %s)",
                    run.run_id,
                    run.parent_agent_id,
                    run.child_agent_id,
                )
                if self.alert_manager:
                    await self.alert_manager.send(make_event(
                        event_type="delegation_timeout",
                        agent_id=run.child_agent_id,
                        detail=f"Delegation run {run.run_id} timed out ({run.parent_agent_id} -> {run.child_agent_id})",
                    ))

            if self.control_plane is not None:
                all_tasks = self.control_plane.task_registry.list()
                orphaned_ids = self.health.inspect_orphaned_tasks(all_tasks, self._agents.keys())
                for orphan_id in orphaned_ids:
                    orphan = self.control_plane.task_registry.get(orphan_id)
                    if orphan is None:
                        continue
                    if orphan.is_terminal:
                        continue
                    self.control_plane.task_registry.fail(
                        orphan_id,
                        f"Orphaned task: assigned agent {orphan.agent_id} is not active",
                    )
                    logger.warning("Marked orphaned control-plane task as failed: %s", orphan_id)
                    if self.alert_manager:
                        await self.alert_manager.send(make_event(
                            event_type="task_orphaned",
                            agent_id=orphan.agent_id or "unknown",
                            detail=f"Control-plane task {orphan_id} became orphaned",
                        ))

            # Monitor ChatBridge liveness
            if self.chat_bridge and self.alert_manager:
                bridge_running = getattr(self.chat_bridge, "is_running", False)
                if self._chat_bridge_was_running and not bridge_running:
                    await self.alert_manager.send(make_event(
                        event_type="bridge_stopped",
                        agent_id="chat_bridge",
                        detail="ChatBridge polling has stopped unexpectedly",
                    ))
                self._chat_bridge_was_running = bridge_running
