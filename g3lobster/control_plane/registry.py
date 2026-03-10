"""In-memory task registry for control-plane work units."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set

from g3lobster.control_plane.task_unit import TaskUnit, TaskUnitStatus


class TaskRegistry:
    """Stores active and historical TaskUnit records with parent-child links."""

    def __init__(self, max_tasks: int = 5000):
        self._tasks: Dict[str, TaskUnit] = {}
        self._children: Dict[str, List[str]] = defaultdict(list)
        self._events: Dict[str, asyncio.Event] = {}
        self._max_tasks = max_tasks

    def add(self, task: TaskUnit) -> TaskUnit:
        self._tasks[task.id] = task
        if task.parent_id:
            self._children[task.parent_id].append(task.id)
        self._events.setdefault(task.id, asyncio.Event())
        self._trim_terminal_tasks()
        return task

    def upsert(self, task: TaskUnit) -> TaskUnit:
        if task.id in self._tasks:
            self._tasks[task.id] = task
            return task
        return self.add(task)

    def get(self, task_id: str) -> Optional[TaskUnit]:
        return self._tasks.get(task_id)

    def list(
        self,
        *,
        status: Optional[TaskUnitStatus] = None,
        agent_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        source: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TaskUnit]:
        items = list(self._tasks.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if agent_id is not None:
            items = [item for item in items if item.agent_id == agent_id]
        if parent_id is not None:
            items = [item for item in items if item.parent_id == parent_id]
        if source is not None:
            items = [item for item in items if item.source == source]

        items.sort(key=lambda item: item.created_at, reverse=True)
        if limit is not None:
            return items[:limit]
        return items

    def children(self, parent_id: str) -> List[TaskUnit]:
        ids = self._children.get(parent_id, [])
        return [self._tasks[item_id] for item_id in ids if item_id in self._tasks]

    def descendants(self, root_id: str) -> List[TaskUnit]:
        result: List[TaskUnit] = []
        stack = [root_id]
        seen: Set[str] = set()

        while stack:
            parent = stack.pop()
            for child_id in self._children.get(parent, []):
                if child_id in seen:
                    continue
                seen.add(child_id)
                child = self._tasks.get(child_id)
                if child is None:
                    continue
                result.append(child)
                stack.append(child.id)

        return result

    def transition(
        self,
        task_id: str,
        status: TaskUnitStatus,
        *,
        agent_id: Optional[str] = None,
        result_md: Optional[str] = None,
        error: Optional[str] = None,
    ) -> TaskUnit:
        task = self._tasks[task_id]
        task.transition(status, agent_id=agent_id, result_md=result_md, error=error)
        if task.is_terminal:
            self._events.setdefault(task.id, asyncio.Event()).set()
        return task

    def complete(self, task_id: str, result_md: str) -> TaskUnit:
        return self.transition(task_id, TaskUnitStatus.COMPLETED, result_md=result_md)

    def fail(self, task_id: str, error: str) -> TaskUnit:
        return self.transition(task_id, TaskUnitStatus.FAILED, error=error)

    def cancel(self, task_id: str, error: Optional[str] = None) -> TaskUnit:
        return self.transition(task_id, TaskUnitStatus.CANCELLED, error=error)

    def cancel_tree(self, task_id: str, reason: str = "cancelled by request") -> List[TaskUnit]:
        cancelled: List[TaskUnit] = []
        root = self.get(task_id)
        if root and not root.is_terminal:
            cancelled.append(self.cancel(task_id, error=reason))

        for child in self.descendants(task_id):
            if child.is_terminal:
                continue
            cancelled.append(self.cancel(child.id, error=reason))

        return cancelled

    async def wait_for_terminal(self, task_id: str, timeout_s: Optional[float] = None) -> TaskUnit:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")
        if task.is_terminal:
            return task

        event = self._events.setdefault(task_id, asyncio.Event())
        await asyncio.wait_for(event.wait(), timeout=timeout_s)
        final = self.get(task_id)
        if final is None:
            raise KeyError(f"Task {task_id} not found")
        return final

    def summary(self) -> Dict[str, object]:
        counts = {status.value: 0 for status in TaskUnitStatus}
        for task in self._tasks.values():
            counts[task.status.value] += 1

        return {
            "total": len(self._tasks),
            "by_status": counts,
            "roots": len([task for task in self._tasks.values() if task.parent_id is None]),
        }

    def find_orphaned(self, active_agent_ids: Iterable[str]) -> List[TaskUnit]:
        active = set(active_agent_ids)
        orphaned: List[TaskUnit] = []

        for task in self._tasks.values():
            if task.status not in {TaskUnitStatus.QUEUED, TaskUnitStatus.WORKING}:
                continue
            if not task.agent_id:
                continue
            if task.agent_id in active:
                continue
            orphaned.append(task)

        return orphaned

    def _trim_terminal_tasks(self) -> None:
        overflow = len(self._tasks) - self._max_tasks
        if overflow <= 0:
            return

        terminals = [task for task in self._tasks.values() if task.is_terminal]
        terminals.sort(key=lambda item: item.completed_at or item.created_at)

        for task in terminals[:overflow]:
            self._tasks.pop(task.id, None)
            self._events.pop(task.id, None)
            if task.parent_id and task.parent_id in self._children:
                self._children[task.parent_id] = [
                    child_id for child_id in self._children[task.parent_id] if child_id != task.id
                ]
