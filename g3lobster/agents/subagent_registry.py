"""Persistent registry for cross-agent delegation runs."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    REGISTERED = "registered"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class SubagentRun:
    run_id: str
    parent_agent_id: str
    child_agent_id: str
    task: str
    session_id: str
    parent_session_id: str
    status: RunStatus = RunStatus.REGISTERED
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timeout_s: float = 300.0


class SubagentRegistry:
    """Stores subagent delegation run metadata with JSON persistence."""

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir).expanduser().resolve()
        self._runs: Dict[str, SubagentRun] = {}
        self._registry_file = self._data_dir / ".subagent_runs.json"
        self._load_from_disk()

    def register_run(
        self,
        parent_agent_id: str,
        child_agent_id: str,
        task: str,
        parent_session_id: str,
        timeout_s: float = 300.0,
    ) -> SubagentRun:
        normalized_parent = str(parent_agent_id).strip()
        normalized_child = str(child_agent_id).strip()
        normalized_task = str(task).strip()
        normalized_parent_session_id = str(parent_session_id).strip()
        normalized_timeout_s = float(timeout_s)
        if not normalized_parent:
            raise ValueError("parent_agent_id is required")
        if not normalized_child:
            raise ValueError("child_agent_id is required")
        if not normalized_task:
            raise ValueError("task is required")
        if not normalized_parent_session_id:
            raise ValueError("parent_session_id is required")
        if normalized_timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0")
        if normalized_parent == normalized_child:
            raise ValueError("Circular delegation is not allowed")

        run = SubagentRun(
            run_id=str(uuid.uuid4()),
            parent_agent_id=normalized_parent,
            child_agent_id=normalized_child,
            task=normalized_task,
            session_id=f"delegation-{uuid.uuid4().hex[:8]}",
            parent_session_id=normalized_parent_session_id,
            timeout_s=normalized_timeout_s,
        )
        self._runs[run.run_id] = run
        self._save_to_disk()
        logger.info(
            "Registered subagent run %s: %s -> %s",
            run.run_id,
            normalized_parent,
            normalized_child,
        )
        return run

    def mark_running(self, run_id: str) -> Optional[SubagentRun]:
        run = self._runs.get(run_id)
        if not run:
            return None
        run.status = RunStatus.RUNNING
        self._save_to_disk()
        return run

    def complete_run(self, run_id: str, result: str) -> Optional[SubagentRun]:
        run = self._runs.get(run_id)
        if not run:
            return None
        run.status = RunStatus.COMPLETED
        run.result = result
        run.error = None
        run.completed_at = time.time()
        self._save_to_disk()
        return run

    def fail_run(self, run_id: str, error: str) -> Optional[SubagentRun]:
        run = self._runs.get(run_id)
        if not run:
            return None
        run.status = RunStatus.FAILED
        run.error = str(error).strip() or "Unknown failure"
        run.completed_at = time.time()
        self._save_to_disk()
        return run

    def check_timeouts(self) -> List[SubagentRun]:
        timed_out: List[SubagentRun] = []
        now = time.time()
        for run in self._runs.values():
            if run.status != RunStatus.RUNNING:
                continue
            if (now - run.created_at) <= run.timeout_s:
                continue
            run.status = RunStatus.TIMED_OUT
            run.error = f"Timed out after {run.timeout_s:.1f}s"
            run.completed_at = now
            timed_out.append(run)
        if timed_out:
            self._save_to_disk()
        return timed_out

    def get_run(self, run_id: str) -> Optional[SubagentRun]:
        return self._runs.get(run_id)

    def list_runs(self, parent_agent_id: Optional[str] = None) -> List[SubagentRun]:
        runs = list(self._runs.values())
        if parent_agent_id:
            normalized = str(parent_agent_id).strip()
            runs = [run for run in runs if run.parent_agent_id == normalized]
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    def _save_to_disk(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = {}
        for run_id, run in self._runs.items():
            record = asdict(run)
            record["status"] = run.status.value
            payload[run_id] = record
        self._registry_file.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _load_from_disk(self) -> None:
        if not self._registry_file.exists():
            return

        try:
            payload = json.loads(self._registry_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Failed to decode subagent registry file, starting fresh")
            return

        if not isinstance(payload, dict):
            logger.warning("Subagent registry payload is not a JSON object, starting fresh")
            return

        loaded: Dict[str, SubagentRun] = {}
        for run_id, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            try:
                record = dict(raw)
                record["status"] = RunStatus(str(record.get("status", RunStatus.REGISTERED.value)))
                record["run_id"] = str(record.get("run_id") or run_id)
                loaded[record["run_id"]] = SubagentRun(
                    **{
                        key: value
                        for key, value in record.items()
                        if key in SubagentRun.__dataclass_fields__
                    }
                )
            except Exception:
                logger.warning("Skipping invalid subagent run record: %s", run_id)
                continue

        self._runs = loaded
