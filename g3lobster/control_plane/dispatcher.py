"""Capacity-aware task dispatch for control-plane TaskUnits."""

from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable, Dict, Optional

from g3lobster.control_plane.registry import TaskRegistry
from g3lobster.control_plane.task_unit import TaskUnit, TaskUnitStatus
from g3lobster.tasks.types import Task, TaskStatus

CompletionCallback = Callable[[TaskUnit], Optional[Awaitable[None]]]


class BackpressureError(RuntimeError):
    """Raised when all candidate agent queues are at capacity."""


class Dispatcher:
    """Selects an agent and executes a TaskUnit with queue/backpressure awareness."""

    def __init__(
        self,
        *,
        agent_registry,
        task_registry: TaskRegistry,
        max_queue_depth: int = 5,
        tmux_spawner: Optional[object] = None,
    ):
        self.agent_registry = agent_registry
        self.task_registry = task_registry
        self.max_queue_depth = max(1, int(max_queue_depth))
        self.tmux_spawner = tmux_spawner
        self._inflight: Dict[str, asyncio.Task] = {}
        self._on_task_complete: Optional[CompletionCallback] = None

    def set_on_task_complete(self, callback: CompletionCallback) -> None:
        self._on_task_complete = callback

    async def dispatch(
        self,
        task: TaskUnit,
        *,
        preferred_agent_id: Optional[str] = None,
        on_complete: Optional[CompletionCallback] = None,
    ) -> str:
        runtime = await self._select_runtime(preferred_agent_id)

        self.task_registry.transition(
            task.id,
            TaskUnitStatus.QUEUED,
            agent_id=runtime.id,
        )

        worker = asyncio.create_task(
            self._run_task(task, runtime, on_complete=on_complete),
            name=f"control-plane-task-{task.id[:8]}",
        )
        self._inflight[task.id] = worker

        def _cleanup(_: asyncio.Task) -> None:
            self._inflight.pop(task.id, None)

        worker.add_done_callback(_cleanup)
        return runtime.id

    async def cancel(self, task_id: str) -> bool:
        current = self.task_registry.get(task_id)
        if current is None or current.is_terminal:
            return False

        running = self._inflight.get(task_id)
        if running and not running.done():
            running.cancel()

        self.task_registry.cancel(task_id, error="cancelled by request")
        return True

    def snapshot(self) -> Dict[str, object]:
        queue_depths: Dict[str, Dict[str, int]] = {}
        active = list(self.agent_registry.active_agents())

        for runtime in active:
            queue_depths[runtime.id] = {
                "queue_depth": int(getattr(runtime, "pending_assignments", 0)),
                "load": self._agent_load(runtime),
            }

        return {
            "queue_depths": queue_depths,
            "inflight": sorted(self._inflight.keys()),
        }

    async def _run_task(
        self,
        task: TaskUnit,
        runtime,
        *,
        on_complete: Optional[CompletionCallback] = None,
    ) -> None:
        callback = on_complete or self._on_task_complete

        long_running = bool(task.metadata.get("long_running", False))
        if long_running and self.tmux_spawner is not None:
            await self._run_tmux_task(task, runtime.id)
            await self._invoke_callback(callback, task)
            return

        session_id = str(task.metadata.get("session_id") or f"control-plane-{task.id[:8]}")
        timeout_s = float(task.metadata.get("timeout_s") or 120.0)

        execution_task = Task(
            prompt=task.prompt,
            session_id=session_id,
            timeout_s=timeout_s,
        )

        def _mark_working() -> None:
            current = self.task_registry.get(task.id)
            if current is None or current.status != TaskUnitStatus.QUEUED:
                return
            self.task_registry.transition(task.id, TaskUnitStatus.WORKING)

        try:
            result = await runtime.assign(execution_task, on_started=_mark_working)
        except asyncio.CancelledError:
            current = self.task_registry.get(task.id)
            if current and not current.is_terminal:
                self.task_registry.cancel(task.id, error="cancelled while queued")
            raise
        except Exception as exc:
            current = self.task_registry.get(task.id)
            if current and not current.is_terminal:
                if current.status == TaskUnitStatus.QUEUED:
                    self.task_registry.transition(task.id, TaskUnitStatus.WORKING)
                self.task_registry.fail(task.id, str(exc))
            await self._invoke_callback(callback, task)
            return

        current = self.task_registry.get(task.id)
        if current is None:
            await self._invoke_callback(callback, task)
            return
        if current.is_terminal:
            await self._invoke_callback(callback, current)
            return
        if current.status == TaskUnitStatus.QUEUED:
            self.task_registry.transition(task.id, TaskUnitStatus.WORKING)

        if result.status == TaskStatus.COMPLETED:
            self.task_registry.complete(task.id, result.result or "")
        elif result.status == TaskStatus.FAILED:
            self.task_registry.fail(task.id, result.error or "unknown failure")
        elif result.status == TaskStatus.CANCELED:
            self.task_registry.cancel(task.id, error=result.error or "cancelled")
        else:
            self.task_registry.fail(task.id, f"unexpected task status: {result.status.value}")

        latest = self.task_registry.get(task.id)
        if latest is not None:
            await self._invoke_callback(callback, latest)

    async def _run_tmux_task(self, task: TaskUnit, agent_id: str) -> None:
        current = self.task_registry.get(task.id)
        if current and current.status == TaskUnitStatus.QUEUED:
            self.task_registry.transition(task.id, TaskUnitStatus.WORKING)

        try:
            session_name = await self.tmux_spawner.spawn(
                agent_id=agent_id,
                task_id=task.id,
                prompt=task.prompt,
                session_id=str(task.metadata.get("session_id") or f"control-plane-{task.id[:8]}"),
            )
            metadata = self.task_registry.get(task.id).metadata if self.task_registry.get(task.id) else {}
            metadata["tmux_session"] = session_name
            self.task_registry.complete(
                task.id,
                f"Spawned long-running tmux session `{session_name}`.",
            )
        except Exception as exc:
            self.task_registry.fail(task.id, str(exc))

    async def _select_runtime(self, preferred_agent_id: Optional[str]):
        if preferred_agent_id:
            runtime = self.agent_registry.get_agent(preferred_agent_id)
            if runtime is None:
                started = await self.agent_registry.start_agent(preferred_agent_id)
                if not started:
                    raise RuntimeError(f"Agent {preferred_agent_id} is not available")
                runtime = self.agent_registry.get_agent(preferred_agent_id)
            if runtime and self._has_capacity(runtime):
                return runtime

        candidates = list(self.agent_registry.active_agents())
        if not candidates:
            raise RuntimeError("No active agents available for dispatch")

        candidates.sort(key=self._agent_load)
        for runtime in candidates:
            if self._has_capacity(runtime):
                return runtime

        raise BackpressureError("All agent queues are full")

    def _has_capacity(self, runtime) -> bool:
        return self._agent_load(runtime) < self.max_queue_depth

    @staticmethod
    def _agent_load(runtime) -> int:
        pending = int(getattr(runtime, "pending_assignments", 0))
        current = 1 if getattr(runtime, "current_task", None) is not None else 0
        return pending + current

    async def _invoke_callback(self, callback: Optional[CompletionCallback], task: TaskUnit) -> None:
        if callback is None:
            return
        result = callback(task)
        if inspect.isawaitable(result):
            await result
