"""Gemini agent implementation for pooled execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from g3lobster.cli.parser import clean_text, split_reasoning, strip_reasoning
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.manager import MemoryManager
from g3lobster.mcp.manager import MCPManager
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus, TaskStore

logger = logging.getLogger(__name__)

STALE_TASK_THRESHOLD_S = 6 * 60 * 60
CRON_PATTERN_WINDOW_S = 7 * 24 * 60 * 60


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
        board_store: Optional[object] = None,
        event_bus: Optional[object] = None,
        heartbeat_interval_s: float = 30.0,
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
        self.board_store = board_store
        self.event_bus = event_bus
        self.heartbeat_interval_s = max(5.0, float(heartbeat_interval_s or 30.0))
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_stop_event: Optional[asyncio.Event] = None

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
        self._start_heartbeat_loop()

    def is_alive(self) -> bool:
        return bool(self.process and self.process.is_alive())

    async def stop(self) -> None:
        await self._stop_heartbeat_loop()
        if self.process:
            await self.process.kill()
        self.state = AgentState.STOPPED

    def _start_heartbeat_loop(self) -> None:
        if self.board_store is None or self.event_bus is None:
            return
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_stop_event = asyncio.Event()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"g3lobster-heartbeat-review-{self.id}",
        )

    async def _stop_heartbeat_loop(self) -> None:
        if self._heartbeat_task is None:
            return
        if self._heartbeat_stop_event is not None:
            self._heartbeat_stop_event.set()
        self._heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._heartbeat_task
        self._heartbeat_task = None
        self._heartbeat_stop_event = None

    @staticmethod
    def _parse_iso_ts(raw: str) -> datetime:
        text = str(raw or "").strip()
        if not text:
            return datetime.fromtimestamp(0, tz=timezone.utc)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return datetime.fromtimestamp(0, tz=timezone.utc)

    def _extract_goals_and_directives(self) -> List[str]:
        snippets: List[str] = []
        memory = ""
        procedures = ""
        try:
            memory = self.memory_manager.read_memory()
        except Exception:
            logger.debug("Failed reading MEMORY.md for heartbeat suggestions", exc_info=True)
        try:
            procedures = self.memory_manager.read_procedures()
        except Exception:
            logger.debug("Failed reading PROCEDURES.md for heartbeat suggestions", exc_info=True)

        for source in (memory, procedures):
            for line in source.splitlines():
                normalized = line.strip()
                if not normalized:
                    continue
                lowered = normalized.lower()
                if any(token in lowered for token in ("goal", "directive", "priority", "must")):
                    snippets.append(normalized.lstrip("#- ").strip())
                if len(snippets) >= 5:
                    return snippets
        return snippets

    def _build_heartbeat_suggestions(self, tasks: List[object], goals: List[str]) -> List[Dict[str, Any]]:
        now = datetime.now(tz=timezone.utc)
        suggestions: List[Dict[str, Any]] = []

        normalized = sorted(
            tasks,
            key=lambda item: self._parse_iso_ts(getattr(item, "updated_at", "")),
        )

        open_tasks = [
            item
            for item in normalized
            if str(getattr(item, "status", "")).strip() in {"todo", "in_progress"}
        ]
        for item in open_tasks:
            updated_at = self._parse_iso_ts(getattr(item, "updated_at", ""))
            age_s = max(0, int((now - updated_at).total_seconds()))
            if age_s < STALE_TASK_THRESHOLD_S:
                continue
            suggestions.append({
                "kind": "stale",
                "task_id": str(getattr(item, "id", "")),
                "title": str(getattr(item, "title", "")),
                "message": (
                    f"Task '{getattr(item, 'title', 'Untitled')}' has been idle for "
                    f"{age_s // 3600}h. Escalate or re-scope."
                ),
            })
            if len([s for s in suggestions if s["kind"] == "stale"]) >= 3:
                break

        blocked = [
            item
            for item in normalized
            if str(getattr(item, "status", "")).strip() == "blocked"
        ]
        for item in blocked[:3]:
            suggestions.append({
                "kind": "blocked",
                "task_id": str(getattr(item, "id", "")),
                "title": str(getattr(item, "title", "")),
                "message": f"Task '{getattr(item, 'title', 'Untitled')}' is blocked. Request input or unblock dependency.",
            })

        done_tasks = [
            item
            for item in normalized
            if str(getattr(item, "status", "")).strip() == "done"
        ]
        if done_tasks:
            latest_done = sorted(
                done_tasks,
                key=lambda item: self._parse_iso_ts(getattr(item, "updated_at", "")),
                reverse=True,
            )[0]
            suggestions.append({
                "kind": "next_step",
                "task_id": str(getattr(latest_done, "id", "")),
                "title": str(getattr(latest_done, "title", "")),
                "message": (
                    f"Completed '{getattr(latest_done, 'title', 'Untitled')}'. "
                    "Capture result and queue the next concrete step."
                ),
            })

        recent_done = [
            item
            for item in done_tasks
            if (now - self._parse_iso_ts(getattr(item, "updated_at", ""))).total_seconds() <= CRON_PATTERN_WINDOW_S
        ]
        if len(recent_done) >= 3:
            title_tokens = [
                str(getattr(item, "title", "")).strip().split(" ", 1)[0].lower()
                for item in recent_done
                if str(getattr(item, "title", "")).strip()
            ]
            if title_tokens:
                token, count = Counter(title_tokens).most_common(1)[0]
                if token and count >= 2:
                    suggestions.append({
                        "kind": "cron_candidate",
                        "message": (
                            f"Detected repeated completed tasks starting with '{token}' "
                            f"({count} times in 7d). Consider a recurring cron."
                        ),
                    })

        if goals and open_tasks:
            first_goal = goals[0]
            suggestions.append({
                "kind": "next_step",
                "message": f"Current directive check: align active tasks with '{first_goal}'.",
            })

        return suggestions[:8]

    async def run_heartbeat_review(self) -> Optional[Dict[str, Any]]:
        """Compute and publish heartbeat-driven board suggestions."""
        if self.board_store is None or self.event_bus is None:
            return None

        tasks = await asyncio.to_thread(
            self.board_store.list_items,
            None,
            None,
            self.id,
            None,
            None,
            200,
        )
        goals = await asyncio.to_thread(self._extract_goals_and_directives)
        suggestions = self._build_heartbeat_suggestions(tasks, goals)
        if not suggestions:
            return None

        event = {
            "type": "heartbeat_review",
            "agent_id": self.id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "suggestions": suggestions,
            "goals": goals[:3],
            "task_count": len(tasks),
        }
        self.event_bus.publish(self.id, event)
        self.event_bus.publish("__board__", event)
        return event

    async def _heartbeat_loop(self) -> None:
        stop_event = self._heartbeat_stop_event or asyncio.Event()
        while not stop_event.is_set():
            try:
                await self.run_heartbeat_review()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Heartbeat review failed for agent %s", self.id, exc_info=True)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_interval_s)
            except asyncio.TimeoutError:
                continue

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
            cleaned = clean_text(raw_output)
            reasoning, parsed = split_reasoning(cleaned)
            task.result = parsed
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.add_event("completed", {"chars": len(parsed or "")})
            if reasoning:
                task.add_event("reasoning", {"text": reasoning})
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
                reasoning, response = split_reasoning(parsed)
                task.result = response
                task.status = TaskStatus.COMPLETED
                task.add_event("completed", {"chars": len(response)})
                if reasoning:
                    task.add_event("reasoning", {"text": reasoning})
                self.memory_manager.append_message(task.session_id, "assistant", response, {"task_id": task.id}, space_id=task.space_id)
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
