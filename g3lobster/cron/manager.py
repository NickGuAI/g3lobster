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
from g3lobster.tasks.types import Task, TaskStatus

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry
    from g3lobster.incident.store import IncidentStore

logger = logging.getLogger(__name__)

_INCIDENT_PROMPT_PREFIX = "__INCIDENT_PROMPT__"


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
        standup_orchestrator: Optional[object] = None,
        incident_store: Optional["IncidentStore"] = None,
        focus_checker=None,
        calendar_cron_schedule: Optional[str] = None,
    ) -> None:
        self._store = cron_store
        self._registry = registry
        self._incident_store = incident_store
        self._scheduler = None  # initialised lazily to avoid import-time dependency
        self._standup_orchestrator = standup_orchestrator
        self._consolidation_enabled = consolidation_enabled
        self._consolidation_schedule = consolidation_schedule
        self._consolidation_days_window = consolidation_days_window
        self._consolidation_stale_days = consolidation_stale_days
        self._gemini_command = gemini_command
        self._gemini_args = gemini_args
        self._gemini_timeout_s = gemini_timeout_s
        self._gemini_cwd = gemini_cwd
        self._focus_checker = focus_checker
        self._calendar_cron_schedule = calendar_cron_schedule

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
        self._register_calendar_job()
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
        self._register_calendar_job()

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
                args=[task.agent_id, task.id, task.instruction, task.dm_target],
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

    def _register_calendar_job(self) -> None:
        """Register the periodic calendar focus-time check if configured."""
        if not self._focus_checker or not self._calendar_cron_schedule:
            return
        scheduler = self._get_scheduler()
        if scheduler is None:
            return
        try:
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            return

        job_id = "calendar_focus_check"
        if scheduler.get_job(job_id):
            return

        try:
            trigger = CronTrigger.from_crontab(self._calendar_cron_schedule)
        except Exception:
            logger.warning("Invalid calendar cron schedule %r — skipping", self._calendar_cron_schedule)
            return

        scheduler.add_job(
            self._check_calendar,
            trigger=trigger,
            id=job_id,
            misfire_grace_time=60,
        )
        logger.info("Registered calendar focus-time check (%s)", self._calendar_cron_schedule)

    async def _check_calendar(self) -> None:
        """Cron callback: refresh focus-time state for all monitored users."""
        logger.debug("Running calendar focus-time check")
        try:
            self._focus_checker.refresh()
        except Exception:
            logger.exception("Calendar focus-time check failed")

    def set_incident_store(self, incident_store: object) -> None:
        """Register an IncidentStore for incident prompt cron tasks."""
        self._incident_store = incident_store

    async def _fire(self, agent_id: str, task_id: str, instruction: str, dm_target: str | None = None) -> None:
        import time as time_mod
        logger.info("Firing cron task %s for agent %s", task_id, agent_id)
        now = datetime.now(tz=timezone.utc).isoformat()
        self._store.update_task(agent_id, task_id, last_run=now)

        # Intercept standup cron instructions before they reach the agent.
        if instruction.startswith("__standup_"):
            from g3lobster.standup.cron_hooks import handle_standup_cron
            start_time = time_mod.monotonic()
            try:
                handled = await handle_standup_cron(instruction, agent_id, self._standup_orchestrator)
                duration = round(time_mod.monotonic() - start_time, 1)
                status = "completed" if handled else "failed"
                preview = f"Standup {instruction} {'handled' if handled else 'not handled'}"
            except Exception:
                duration = round(time_mod.monotonic() - start_time, 1)
                status = "failed"
                preview = "Exception during standup cron execution"
                logger.exception("Standup cron task %s raised an exception", task_id)
            self._store.record_run(agent_id, CronRunRecord(
                task_id=task_id, fired_at=now, status=status,
                duration_s=duration, result_preview=preview,
            ))

        # Handle incident status prompts specially
        if instruction.startswith("__INCIDENT_PROMPT__:"):
            await self._fire_incident_prompt(agent_id, task_id, instruction, now)
            return

        # Handle __INCIDENT_PROMPT__ tasks — rewrite instruction to a status prompt.
        if instruction.startswith(_INCIDENT_PROMPT_PREFIX) and self._incident_store:
            incident_id = instruction[len(_INCIDENT_PROMPT_PREFIX) + 1:]
            incident = self._incident_store.get(agent_id, incident_id)
            if not incident:
                # Incident was deleted or resolved; auto-clean the cron task.
                self._store.delete_task(agent_id, task_id)
                return
            from g3lobster.incident.model import IncidentStatus
            if incident.status != IncidentStatus.ACTIVE:
                self._store.delete_task(agent_id, task_id)
                return
            from g3lobster.incident.formatter import format_status_prompt
            last_ts = incident.timeline[-1].timestamp if incident.timeline else incident.created_at
            try:
                last_dt = datetime.fromisoformat(last_ts)
                minutes = int((datetime.now(tz=timezone.utc) - last_dt).total_seconds() / 60)
            except (ValueError, TypeError):
                minutes = 15
            instruction = format_status_prompt(incident, minutes)

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
            status = "completed" if result.status == TaskStatus.COMPLETED else "failed"
            preview = (result.result or result.error or "")[:200]
            logger.info("Cron task %s completed: status=%s", task_id, result.status)

            # Deliver result to DM target if configured
            if dm_target and status == "completed" and result.result:
                await self._deliver_dm(dm_target, result.result)

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

    async def _deliver_dm(self, dm_target: str, text: str) -> None:
        """Send a cron task result to a user via Google Chat DM."""
        try:
            from g3lobster.chat.auth import get_authenticated_service
            from g3lobster.chat.dm import send_dm

            service = get_authenticated_service()
            await send_dm(service, dm_target, text)
            logger.info("Cron result delivered via DM to %s", dm_target)
        except Exception:
            logger.exception("Failed to deliver cron result DM to %s", dm_target)

    async def _fire_incident_prompt(self, agent_id: str, task_id: str, instruction: str, now: str) -> None:
        """Fire an incident status prompt — sends the prompt text to the agent as a task."""
        incident_id = instruction.split(":", 1)[1] if ":" in instruction else ""
        incident_store = getattr(self, "_incident_store", None)
        if not incident_store or not incident_id:
            logger.warning("Incident prompt fired but no incident store or ID: %s", instruction)
            return

        incident = incident_store.get(agent_id, incident_id)
        if not incident:
            logger.info("Incident %s not found — removing cron task %s", incident_id, task_id)
            self._store.delete_task(agent_id, task_id)
            return

        from g3lobster.incident.model import IncidentStatus
        if incident.status != IncidentStatus.ACTIVE:
            logger.info("Incident %s is not active — removing cron task %s", incident_id, task_id)
            self._store.delete_task(agent_id, task_id)
            return

        from g3lobster.incident.formatter import format_status_prompt
        prompt_text = format_status_prompt(incident)

        runtime = self._registry.get_agent(agent_id)
        if not runtime:
            started = await self._registry.start_agent(agent_id)
            if not started:
                return
            runtime = self._registry.get_agent(agent_id)
            if not runtime:
                return

        session_id = f"cron__{agent_id}"
        task = Task(prompt=prompt_text, session_id=session_id)
        try:
            await runtime.assign(task)
            self._store.record_run(agent_id, CronRunRecord(
                task_id=task_id, fired_at=now, status="completed",
                duration_s=0.0, result_preview=f"Incident prompt for {incident_id[:8]}",
            ))
        except Exception:
            logger.exception("Incident prompt task %s raised an exception", task_id)
