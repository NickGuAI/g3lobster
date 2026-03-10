"""Plan orchestration and result aggregation for control-plane tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from g3lobster.control_plane.dispatcher import BackpressureError, Dispatcher
from g3lobster.control_plane.registry import TaskRegistry
from g3lobster.control_plane.task_unit import TaskUnit, TaskUnitStatus
from g3lobster.control_plane.types import Plan, PlanStep


@dataclass
class _PlanRun:
    plan: Plan
    parent_task_id: str
    step_to_task: Dict[str, str] = field(default_factory=dict)
    task_to_step: Dict[str, str] = field(default_factory=dict)
    dispatched_steps: set[str] = field(default_factory=set)


class Orchestrator:
    """Executes multi-step plans and finalizes parent tasks."""

    def __init__(self, *, task_registry: TaskRegistry, dispatcher: Dispatcher):
        self.task_registry = task_registry
        self.dispatcher = dispatcher
        self._plan_runs: Dict[str, _PlanRun] = {}
        self._lock = asyncio.Lock()

    async def execute_plan(self, plan: Plan, requester_task: TaskUnit) -> None:
        self.task_registry.upsert(requester_task)
        current = self.task_registry.get(requester_task.id)
        if current is not None and current.status == TaskUnitStatus.SUBMITTED:
            self.task_registry.transition(requester_task.id, TaskUnitStatus.QUEUED)
            self.task_registry.transition(requester_task.id, TaskUnitStatus.WORKING)
        self._plan_runs[requester_task.id] = _PlanRun(plan=plan, parent_task_id=requester_task.id)
        await self._dispatch_ready_steps(requester_task.id)
        await self._maybe_finalize_parent(requester_task.id)

    async def on_task_complete(self, task: TaskUnit) -> None:
        if not task.parent_id:
            return

        async with self._lock:
            if task.parent_id in self._plan_runs:
                await self._dispatch_ready_steps(task.parent_id)
            await self._maybe_finalize_parent(task.parent_id)

    async def _dispatch_ready_steps(self, parent_task_id: str) -> None:
        plan_run = self._plan_runs.get(parent_task_id)
        if not plan_run:
            return

        completed_steps = self._completed_steps(plan_run)

        for step in plan_run.plan.steps:
            if step.id in plan_run.dispatched_steps:
                continue
            if not all(dep in completed_steps for dep in step.depends_on):
                continue

            subtask = self._make_subtask(parent_task_id, step)
            self.task_registry.add(subtask)

            try:
                await self.dispatcher.dispatch(
                    subtask,
                    preferred_agent_id=step.agent_id,
                    on_complete=self.on_task_complete,
                )
            except BackpressureError as exc:
                self.task_registry.fail(subtask.id, str(exc))
            except Exception as exc:
                self.task_registry.fail(subtask.id, str(exc))

            plan_run.dispatched_steps.add(step.id)
            plan_run.step_to_task[step.id] = subtask.id
            plan_run.task_to_step[subtask.id] = step.id

    def _completed_steps(self, plan_run: _PlanRun) -> set[str]:
        completed: set[str] = set()
        for step_id, task_id in plan_run.step_to_task.items():
            task = self.task_registry.get(task_id)
            if task is None:
                continue
            if task.status == TaskUnitStatus.COMPLETED:
                completed.add(step_id)
        return completed

    async def _maybe_finalize_parent(self, parent_task_id: str) -> None:
        parent = self.task_registry.get(parent_task_id)
        if parent is None or parent.is_terminal:
            return

        children = self.task_registry.children(parent_task_id)
        if not children:
            return

        if any(not child.is_terminal for child in children):
            return

        plan_run = self._plan_runs.get(parent_task_id)
        if plan_run and len(plan_run.dispatched_steps) < len(plan_run.plan.steps):
            # Some steps never dispatched due failed dependencies.
            missing = [
                step.id
                for step in plan_run.plan.steps
                if step.id not in plan_run.dispatched_steps
            ]
            self.task_registry.fail(
                parent_task_id,
                f"Plan could not dispatch dependent steps: {', '.join(missing)}",
            )
            return

        has_failure = any(child.status != TaskUnitStatus.COMPLETED for child in children)
        aggregated = self._aggregate(parent_task_id, children)

        if has_failure:
            self.task_registry.fail(parent_task_id, aggregated)
        else:
            self.task_registry.complete(parent_task_id, aggregated)

    def _aggregate(self, parent_task_id: str, children: List[TaskUnit]) -> str:
        plan_run = self._plan_runs.get(parent_task_id)
        step_labels: Dict[str, str] = {}
        if plan_run:
            step_labels = {task_id: step_id for step_id, task_id in plan_run.step_to_task.items()}

        sections: List[str] = []
        for child in sorted(children, key=lambda item: item.created_at):
            label = step_labels.get(child.id, child.id)
            if child.status == TaskUnitStatus.COMPLETED:
                body = child.result_md or ""
            elif child.status == TaskUnitStatus.CANCELLED:
                body = f"[cancelled] {child.error or 'task cancelled'}"
            else:
                body = f"[failed] {child.error or 'task failed'}"
            sections.append(f"### {label}\n\n{body}".strip())

        return "\n\n".join(section for section in sections if section).strip()

    def _make_subtask(self, parent_task_id: str, step: PlanStep) -> TaskUnit:
        return TaskUnit(
            prompt=step.prompt,
            source="orchestrator",
            parent_id=parent_task_id,
            metadata=dict(step.metadata),
        )
