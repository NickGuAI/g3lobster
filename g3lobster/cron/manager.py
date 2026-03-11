"""Cron scheduler that fires task prompts to agents on schedule.

Uses APScheduler's AsyncIOScheduler so it integrates cleanly with the
FastAPI/asyncio event loop.  Each enabled :class:`~g3lobster.cron.store.CronTask`
is scheduled with a ``CronTrigger`` derived from its ``schedule`` field
(standard 5-field cron expression).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from g3lobster.cron.store import CronRunRecord, CronStore
from g3lobster.tasks.types import Task

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


class CronManager:
    """Wraps APScheduler to run per-agent cron tasks."""

    def __init__(
        self,
        cron_store: CronStore,
        registry: "AgentRegistry",
        consolidation_enabled: bool = False,
        consolidation_schedule: str = "0 2 * * *",
        consolidation_days_window: int = 7,
        consolidation_stale_days: int = 30,
        gemini_command: str = "gemini",
        gemini_args: Optional[list] = None,
        gemini_timeout_s: float = 45.0,
        gemini_cwd: Optional[str] = None,
    ) -> None:
        self._store = cron_store
        self._registry = registry
        self._scheduler = None  # initialised lazily to avoid import-time dependency
        self._consolidation_enabled = consolidation_enabled
        self._consolidation_schedule = consolidation_schedule
        self._consolidation_days_window = consolidation_days_window
        self._consolidation_stale_days = consolidation_stale_days
        self._gemini_command = gemini_command
        self._gemini_args = gemini_args
        self._gemini_timeout_s = gemini_timeout_s
        self._gemini_cwd = gemini_cwd

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
        self._scheduler = None

    def reload(self) -> None:
        """Re-read the store and re-sync all scheduled jobs."""
        scheduler = self._get_scheduler()
        if scheduler is None:
            return
        if not scheduler.running:
            logger.debug("CronManager.reload() called before start() — skipping")
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

        # Schedule system-level consolidation job
        if self._consolidation_enabled:
            consolidation_job_id = "system_consolidation"
            if not scheduler.get_job(consolidation_job_id):
                try:
                    trigger = CronTrigger.from_crontab(self._consolidation_schedule)
                    scheduler.add_job(
                        self._fire_consolidation,
                        trigger=trigger,
                        id=consolidation_job_id,
                        misfire_grace_time=300,
                    )
                    logger.info(
                        "Scheduled nightly consolidation (%s)", self._consolidation_schedule
                    )
                except Exception:
                    logger.warning(
                        "Invalid consolidation schedule %r — skipping",
                        self._consolidation_schedule,
                    )

    async def _fire(self, agent_id: str, task_id: str, instruction: str) -> None:
        import time as time_mod
        logger.info("Firing cron task %s for agent %s", task_id, agent_id)
        now = datetime.now(tz=timezone.utc).isoformat()
        self._store.update_task(agent_id, task_id, last_run=now)

        runtime = self._registry.get_agent(agent_id)
        if not runtime:
            started = await self._registry.start_agent(agent_id)
            if not started:
                logger.warning("Cron task %s: agent %s could not be started", task_id, agent_id)
                self._store.record_run(agent_id, CronRunRecord(
                    task_id=task_id, fired_at=now, status="failed",
                    duration_s=0.0, result_preview="Agent could not be started",
                ))
                return
            runtime = self._registry.get_agent(agent_id)
            if not runtime:
                return

        session_id = f"cron__{agent_id}"
        task = Task(prompt=instruction, session_id=session_id)
        start_time = time_mod.monotonic()
        try:
            result = await runtime.assign(task)
            duration = round(time_mod.monotonic() - start_time, 1)
            status = "completed" if result.status.value == "completed" else "failed"
            preview = (result.result or result.error or "")[:200]
            logger.info("Cron task %s completed: status=%s", task_id, result.status)
        except Exception:
            duration = round(time_mod.monotonic() - start_time, 1)
            status = "failed"
            preview = "Exception during execution"
            logger.exception("Cron task %s raised an exception", task_id)

        self._store.record_run(agent_id, CronRunRecord(
            task_id=task_id, fired_at=now, status=status,
            duration_s=duration, result_preview=preview,
        ))

    async def _fire_consolidation(self) -> None:
        """Run the nightly consolidation pipeline across all active agents."""
        from g3lobster.memory.consolidation import ConsolidationPipeline

        logger.info("Firing nightly consolidation pipeline")
        pipeline = ConsolidationPipeline(
            gemini_command=self._gemini_command,
            gemini_args=self._gemini_args,
            gemini_timeout_s=self._gemini_timeout_s,
            gemini_cwd=self._gemini_cwd,
            days_window=self._consolidation_days_window,
            stale_days=self._consolidation_stale_days,
        )
        reports = pipeline.run_all(self._registry)
        for report in reports:
            logger.info("Consolidation report [%s]: %s", report.agent_id, report.summary)
