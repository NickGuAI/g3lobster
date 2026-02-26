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
    session_id: str  # child's session ID
    parent_session_id: str  # parent's session for result delivery
    status: RunStatus = RunStatus.REGISTERED
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timeout_s: float = 300.0  # 5 min default


class SubagentRegistry:
    """Manages cross-agent delegation with disk persistence."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._runs: Dict[str, SubagentRun] = {}
        self._registry_file = data_dir / ".subagent_runs.json"
        self._load_from_disk()

    def register_run(
        self,
        parent_agent_id: str,
        child_agent_id: str,
        task: str,
        parent_session_id: str,
        timeout_s: float = 300.0,
    ) -> SubagentRun:
        run = SubagentRun(
            run_id=str(uuid.uuid4()),
            parent_agent_id=parent_agent_id,
            child_agent_id=child_agent_id,
            task=task,
            session_id=f"delegation-{uuid.uuid4().hex[:8]}",
            parent_session_id=parent_session_id,
            timeout_s=timeout_s,
        )
        self._runs[run.run_id] = run
        self._save_to_disk()
        logger.info(
            "Registered subagent run %s: %s -> %s",
            run.run_id,
            parent_agent_id,
            child_agent_id,
        )
        return run

    def complete_run(self, run_id: str, result: str) -> None:
        run = self._runs.get(run_id)
        if not run:
            return
        run.status = RunStatus.COMPLETED
        run.result = result
        run.completed_at = time.time()
        self._save_to_disk()

    def fail_run(self, run_id: str, error: str) -> None:
        run = self._runs.get(run_id)
        if not run:
            return
        run.status = RunStatus.FAILED
        run.error = error
        run.completed_at = time.time()
        self._save_to_disk()

    def check_timeouts(self) -> List[SubagentRun]:
        """Check for timed-out runs. Call periodically."""
        timed_out = []
        now = time.time()
        for run in self._runs.values():
            if run.status == RunStatus.RUNNING and (now - run.created_at) > run.timeout_s:
                run.status = RunStatus.TIMED_OUT
                run.error = f"Timed out after {run.timeout_s}s"
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
            runs = [r for r in runs if r.parent_agent_id == parent_agent_id]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def _save_to_disk(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = {rid: asdict(run) for rid, run in self._runs.items()}
        self._registry_file.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

    def _load_from_disk(self) -> None:
        if not self._registry_file.exists():
            return
        try:
            data = json.loads(self._registry_file.read_text(encoding="utf-8"))
            for rid, d in data.items():
                d["status"] = RunStatus(d["status"])
                self._runs[rid] = SubagentRun(
                    **{k: v for k, v in d.items() if k in SubagentRun.__dataclass_fields__}
                )
        except (json.JSONDecodeError, Exception):
            logger.warning("Failed to load subagent registry, starting fresh")
