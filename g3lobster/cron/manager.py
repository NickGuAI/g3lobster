"""Cron scheduler that fires task prompts to agents on schedule.

Uses APScheduler's AsyncIOScheduler so it integrates cleanly with the
FastAPI/asyncio event loop.  Each enabled :class:`~g3lobster.cron.store.CronTask`
is scheduled with a ``CronTrigger`` derived from its ``schedule`` field
(standard 5-field cron expression).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from g3lobster.cron.store import CronStore
from g3lobster.tasks.types import Task

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


class CronManager:
    """Wraps APScheduler to run per-agent cron tasks."""

    def __init__(self, cron_store: CronStore, registry: "AgentRegistry") -> None:
        self._store = cron_store
        self._registry = registry
        self._scheduler = None  # initialised lazily to avoid import-time dependency

    def _get_scheduler(self):
        if self._scheduler is None:
            try:
                from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
                self._scheduler = AsyncIOScheduler()
            except ImportError:
                logger.warning(
                    "apscheduler is not installed — cron tasks will not run. "
                    "Install it with: pip install apscheduler"
                )
        return self._scheduler

    def start(self) -> None:
        scheduler = self._get_scheduler()
        if scheduler is None:
            return
        self._load_tasks()
        if not scheduler.running:
            scheduler.start()
            logger.info("CronManager started")

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("CronManager stopped")

    def reload(self) -> None:
        """Re-read the store and re-sync all scheduled jobs."""
        scheduler = self._get_scheduler()
        if scheduler is None:
            return
        scheduler.remove_all_jobs()
        self._load_tasks()

    def _load_tasks(self) -> None:
        scheduler = self._get_scheduler()
        if scheduler is None:
            return
        try:
            from apscheduler.triggers.cron import CronTrigger  # type: ignore
        except ImportError:
            return

        for task in self._store.list_all_enabled():
            try:
                trigger = CronTrigger.from_crontab(task.schedule)
            except Exception:
                logger.warning("Invalid cron schedule %r for task %s — skipping", task.schedule, task.id)
                continue

            job_id = f"cron_{task.id}"
            if scheduler.get_job(job_id):
                continue

            scheduler.add_job(
                self._fire,
                trigger=trigger,
                id=job_id,
                args=[task.agent_id, task.id, task.instruction],
                misfire_grace_time=60,
            )
            logger.debug("Scheduled cron task %s (%s) for agent %s", task.id, task.schedule, task.agent_id)

    async def _fire(self, agent_id: str, task_id: str, instruction: str) -> None:
        logger.info("Firing cron task %s for agent %s", task_id, agent_id)
        now = datetime.now(tz=timezone.utc).isoformat()
        self._store.update_task(agent_id, task_id, last_run=now)

        runtime = self._registry.get_agent(agent_id)
        if not runtime:
            started = await self._registry.start_agent(agent_id)
            if not started:
                logger.warning("Cron task %s: agent %s could not be started", task_id, agent_id)
                return
            runtime = self._registry.get_agent(agent_id)
            if not runtime:
                return

        session_id = f"cron__{agent_id}"
        task = Task(prompt=instruction, session_id=session_id)
        try:
            result = await runtime.assign(task)
            logger.info("Cron task %s completed: status=%s", task_id, result.status)
        except Exception:
            logger.exception("Cron task %s raised an exception", task_id)
