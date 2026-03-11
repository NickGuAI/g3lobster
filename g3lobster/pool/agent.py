"""Gemini agent implementation for pooled execution."""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from g3lobster.cli.parser import clean_text, strip_reasoning
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.manager import MemoryManager
from g3lobster.mcp.manager import MCPManager
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus, TaskStore


def _normalize_timeout(timeout_s: Optional[float]) -> Optional[float]:
    if timeout_s is None:
        return None
    timeout_value = float(timeout_s)
    if timeout_value <= 0:
        return None
    return timeout_value


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
        task_store: Optional[TaskStore] = None,
        subagent_spawner: Optional[object] = None,
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
        self.task_store = task_store
        self.subagent_spawner = subagent_spawner

    def _persist_terminal_task(self, task: Task) -> None:
        if not self.task_store:
            return
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}:
            self.task_store.add(task)

    @staticmethod
    def _mark_task_canceled(task: Task, reason: str) -> None:
        if task.status == TaskStatus.CANCELED:
            return
        task.status = TaskStatus.CANCELED
        task.error = reason
        task.completed_at = task.completed_at or time.time()
        task.add_event("canceled", {"reason": reason})

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
            prompt = self.context_builder.build(task.session_id, task.prompt, space_id=task.space_id)
            self.memory_manager.append_message(task.session_id, "user", task.prompt, {"task_id": task.id}, space_id=task.space_id)
            raw_output = await self.process.ask(
                prompt,
                timeout=_normalize_timeout(task.timeout_s),
                session_id=task.session_id,
            )
            parsed = strip_reasoning(clean_text(raw_output))
            task.result = parsed
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.add_event("completed", {"chars": len(parsed or "")})
            self.memory_manager.append_message(task.session_id, "assistant", parsed, {"task_id": task.id}, space_id=task.space_id)
        except Exception as exc:  # pragma: no cover - defensive path
            if task.status != TaskStatus.CANCELED:
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
            self._persist_terminal_task(task)

        return task

    async def assign_stream(self, task: Task):
        """Assign a task and yield streaming events as they arrive.

        Uses ask_stream() when the underlying process supports it and
        falls back to assign() otherwise.
        """
        from g3lobster.cli.streaming import StreamEvent, StreamEventType, accumulate_text

        if not hasattr(self.process, "ask_stream"):
            # Fallback: run non-streaming and yield a single result
            result_task = await self.assign(task)
            if result_task.status == TaskStatus.FAILED:
                yield StreamEvent(
                    event_type=StreamEventType.ERROR,
                    data={
                        "severity": "error",
                        "message": result_task.error or "unknown error",
                    },
                )
                yield StreamEvent(
                    event_type=StreamEventType.RESULT,
                    data={
                        "status": "error",
                        "error": {"message": result_task.error or "unknown error"},
                    },
                )
            else:
                yield StreamEvent(
                    event_type=StreamEventType.RESULT,
                    data={
                        "status": "success",
                        "response": result_task.result or "",
                        "result": result_task.result or "",
                    },
                )
            return

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
            prompt = self.context_builder.build(task.session_id, task.prompt, space_id=task.space_id)
            self.memory_manager.append_message(task.session_id, "user", task.prompt, {"task_id": task.id}, space_id=task.space_id)
            timeout_s = _normalize_timeout(task.timeout_s)

            collected_events = []
            async for event in self.process.ask_stream(prompt, timeout=timeout_s, session_id=task.session_id):
                collected_events.append(event)
                yield event

            parsed = accumulate_text(collected_events)
            result_error = None
            stream_error = None
            for event in collected_events:
                if event.event_type == StreamEventType.RESULT and event.data.get("status") == "error":
                    error_data = event.data.get("error") or {}
                    if isinstance(error_data, dict):
                        result_error = str(error_data.get("message") or "unknown error")
                    else:
                        result_error = str(error_data or "unknown error")
                elif event.event_type == StreamEventType.ERROR and event.data.get("severity") == "error":
                    stream_error = str(event.data.get("message") or event.data.get("error") or "unknown error")

            task.completed_at = time.time()
            if task.status == TaskStatus.CANCELED:
                pass
            elif result_error or (stream_error and not parsed):
                task.error = result_error or stream_error
                task.status = TaskStatus.FAILED
                task.add_event("failed", {"error": task.error})
            else:
                task.result = parsed
                task.status = TaskStatus.COMPLETED
                task.add_event("completed", {"chars": len(parsed)})
                self.memory_manager.append_message(task.session_id, "assistant", parsed, {"task_id": task.id}, space_id=task.space_id)
        except Exception as exc:
            if task.status != TaskStatus.CANCELED:
                task.error = str(exc)
                task.status = TaskStatus.FAILED
                task.completed_at = time.time()
                task.add_event("failed", {"error": task.error})
                yield StreamEvent(
                    event_type=StreamEventType.ERROR,
                    data={"severity": "error", "message": task.error},
                )
        finally:
            self.current_task = None
            self.busy_since = None
            if self.is_alive():
                self.state = AgentState.IDLE
            else:
                self.state = AgentState.DEAD
            self._persist_terminal_task(task)

    async def cancel_task(self, task_id: str) -> Optional[Task]:
        task = self.current_task
        if not task or task.id != task_id:
            return None

        self._mark_task_canceled(task, "Canceled by API request")
        if self.process and hasattr(self.process, "kill"):
            await self.process.kill()
        return task

    async def delegate_to_subagent(
        self,
        prompt: str,
        timeout_s: Optional[float] = None,
        mcp_servers: Optional[List[str]] = None,
        task_id: Optional[str] = None,
    ):
        if not self.subagent_spawner:
            raise RuntimeError("Sub-agent spawner is not configured")
        selected_servers = list(mcp_servers or self.mcp_servers)
        return await self.subagent_spawner.spawn(
            agent_id=self.id,
            prompt=prompt,
            timeout_s=timeout_s,
            mcp_server_names=selected_servers,
            parent_task_id=task_id,
        )
