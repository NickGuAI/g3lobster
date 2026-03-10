"""Spawn and track delegated Gemini CLI runs inside isolated tmux sessions."""

from __future__ import annotations

import asyncio
import copy
import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from g3lobster.cli.process import ALLOWED_MCP_SERVER_NAMES_FLAG, PROMPT_FLAG
from g3lobster.tmux.session import TmuxSession

_EXIT_SENTINEL_RE = re.compile(r"__G3LOBSTER_EXIT_CODE=(\d+)")
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class SubAgentStatus:
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"


@dataclass
class SubAgentRunInfo:
    session_name: str
    agent_id: str
    prompt: str
    mcp_server_names: List[str] = field(default_factory=list)
    parent_task_id: Optional[str] = None
    status: str = SubAgentStatus.RUNNING
    created_at: float = field(default_factory=time.time)
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timeout_s: float = 300.0
    output: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "session_name": self.session_name,
            "agent_id": self.agent_id,
            "prompt": self.prompt,
            "mcp_server_names": list(self.mcp_server_names),
            "parent_task_id": self.parent_task_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "timeout_s": self.timeout_s,
            "output": self.output,
            "error": self.error,
        }


class SubAgentSpawner:
    """Manages sub-agent tmux sessions for parallel delegated prompts."""

    def __init__(
        self,
        command: str,
        args: Optional[Iterable[str]] = None,
        cwd: Optional[str] = None,
        socket_name: str = "g3lobster",
        max_concurrent_per_agent: int = 3,
        default_timeout_s: float = 300.0,
    ):
        self.command = command
        self.args = list(args or [])
        self.cwd = cwd
        self.socket_name = socket_name
        self.max_concurrent_per_agent = max(1, int(max_concurrent_per_agent))
        self.default_timeout_s = max(1.0, float(default_timeout_s))
        self._runs: Dict[str, SubAgentRunInfo] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _safe_agent_id(agent_id: str) -> str:
        cleaned = _SAFE_ID_RE.sub("-", str(agent_id or "").strip()).strip("-")
        return cleaned or "agent"

    def _build_session_name(self, agent_id: str) -> str:
        return f"g3l-{self._safe_agent_id(agent_id)}-{uuid.uuid4().hex[:8]}"

    def _active_count(self, agent_id: str) -> int:
        return sum(
            1
            for run in self._runs.values()
            if run.agent_id == agent_id and run.status == SubAgentStatus.RUNNING
        )

    @staticmethod
    def _strip_exit_sentinel(output: str) -> str:
        lines = [line for line in (output or "").splitlines() if not line.startswith("__G3LOBSTER_EXIT_CODE=")]
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_exit_code(output: str) -> Optional[int]:
        match = _EXIT_SENTINEL_RE.search(output or "")
        if not match:
            return None
        return int(match.group(1))

    def _build_shell_command(
        self,
        agent_id: str,
        session_name: str,
        prompt: str,
        mcp_server_names: Optional[List[str]],
    ) -> str:
        gemini_cmd: List[str] = [self.command, *self.args, PROMPT_FLAG, prompt]
        if mcp_server_names and mcp_server_names != ["*"]:
            gemini_cmd.extend([ALLOWED_MCP_SERVER_NAMES_FLAG, *mcp_server_names])

        gemini_shell = " ".join(shlex.quote(part) for part in gemini_cmd)
        script = "; ".join(
            [
                f"export G3LOBSTER_AGENT_ID={shlex.quote(agent_id)}",
                f"export G3LOBSTER_SESSION_ID={shlex.quote(session_name)}",
                gemini_shell,
                "rc=$?",
                "printf '\\n__G3LOBSTER_EXIT_CODE=%s\\n' \"$rc\"",
            ]
        )
        return f"bash -lc {shlex.quote(script)}"

    async def _refresh_run_locked(self, run: SubAgentRunInfo) -> None:
        if run.status != SubAgentStatus.RUNNING:
            return

        session = TmuxSession(name=run.session_name, socket_name=self.socket_name)
        now = time.time()
        if (now - run.started_at) > run.timeout_s:
            await session.kill()
            run.status = SubAgentStatus.TIMED_OUT
            run.completed_at = now
            run.error = f"Timed out after {run.timeout_s:.1f}s"
            return

        if not await session.exists():
            run.status = SubAgentStatus.FAILED
            run.completed_at = now
            run.error = "tmux session exited unexpectedly"
            return

        pane = await session.capture_pane()
        exit_code = self._extract_exit_code(pane)
        if exit_code is None:
            return

        run.completed_at = now
        run.output = self._strip_exit_sentinel(pane)
        if exit_code == 0:
            run.status = SubAgentStatus.COMPLETED
        else:
            run.status = SubAgentStatus.FAILED
            run.error = f"Sub-agent exited with code {exit_code}"
        await session.kill()

    async def _refresh_locked(self) -> None:
        for run in list(self._runs.values()):
            await self._refresh_run_locked(run)

    async def spawn(
        self,
        agent_id: str,
        prompt: str,
        timeout_s: Optional[float] = None,
        mcp_server_names: Optional[List[str]] = None,
        parent_task_id: Optional[str] = None,
    ) -> SubAgentRunInfo:
        async with self._lock:
            await self._refresh_locked()

            if self._active_count(agent_id) >= self.max_concurrent_per_agent:
                raise RuntimeError(
                    f"Agent '{agent_id}' reached max concurrent sub-agents "
                    f"({self.max_concurrent_per_agent})"
                )

            session_name = self._build_session_name(agent_id)
            timeout = max(1.0, float(timeout_s or self.default_timeout_s))
            run = SubAgentRunInfo(
                session_name=session_name,
                agent_id=agent_id,
                prompt=prompt,
                mcp_server_names=list(mcp_server_names or ["*"]),
                parent_task_id=parent_task_id,
                timeout_s=timeout,
            )

            session = TmuxSession(name=session_name, socket_name=self.socket_name)
            command = self._build_shell_command(
                agent_id=agent_id,
                session_name=session_name,
                prompt=prompt,
                mcp_server_names=run.mcp_server_names,
            )
            await session.create(command=command, cwd=self.cwd)
            self._runs[session_name] = run
            return copy.deepcopy(run)

    async def list_runs(self, agent_id: str, active_only: bool = True) -> List[SubAgentRunInfo]:
        async with self._lock:
            await self._refresh_locked()
            runs = [
                copy.deepcopy(run)
                for run in self._runs.values()
                if run.agent_id == agent_id and (not active_only or run.status == SubAgentStatus.RUNNING)
            ]
            runs.sort(key=lambda item: item.created_at, reverse=True)
            return runs

    async def get_run(self, agent_id: str, session_name: str) -> Optional[SubAgentRunInfo]:
        async with self._lock:
            run = self._runs.get(session_name)
            if not run or run.agent_id != agent_id:
                return None
            await self._refresh_run_locked(run)
            return copy.deepcopy(run)

    async def kill(self, agent_id: str, session_name: str) -> Optional[SubAgentRunInfo]:
        async with self._lock:
            run = self._runs.get(session_name)
            if not run or run.agent_id != agent_id:
                return None

            if run.status == SubAgentStatus.RUNNING:
                session = TmuxSession(name=session_name, socket_name=self.socket_name)
                pane = ""
                try:
                    if await session.exists():
                        pane = await session.capture_pane()
                except Exception:
                    pane = ""
                await session.kill()
                run.output = run.output or self._strip_exit_sentinel(pane)
                run.status = SubAgentStatus.CANCELED
                run.completed_at = time.time()
                run.error = "Killed by API request"

            return copy.deepcopy(run)

    async def kill_agent_runs(self, agent_id: str) -> int:
        async with self._lock:
            sessions = [
                run.session_name
                for run in self._runs.values()
                if run.agent_id == agent_id and run.status == SubAgentStatus.RUNNING
            ]

        killed = 0
        for session_name in sessions:
            run = await self.kill(agent_id=agent_id, session_name=session_name)
            if run is not None:
                killed += 1
        return killed
