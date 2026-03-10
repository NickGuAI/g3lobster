"""Tmux session spawning for long-running control-plane tasks."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class TmuxSession:
    session_name: str
    agent_id: str
    task_id: str
    prompt: str
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)


class TmuxSpawner:
    """Spawns and tracks detached tmux sessions for long-running prompts."""

    def __init__(
        self,
        *,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        session_prefix: str = "g3l",
        max_sessions_per_agent: int = 2,
        idle_ttl_s: float = 1800.0,
    ):
        self.command = command
        self.args = list(args or [])
        self.cwd = cwd
        self.env = dict(env or {})
        self.session_prefix = session_prefix
        self.max_sessions_per_agent = max(1, int(max_sessions_per_agent))
        self.idle_ttl_s = float(idle_ttl_s)
        self._sessions: Dict[str, TmuxSession] = {}

    async def spawn(self, *, agent_id: str, task_id: str, prompt: str, session_id: str) -> str:
        await self._ensure_tmux_available()
        await self.evict_idle_sessions()

        active_for_agent = [item for item in self._sessions.values() if item.agent_id == agent_id]
        if len(active_for_agent) >= self.max_sessions_per_agent:
            raise RuntimeError(f"tmux session limit reached for agent {agent_id}")

        session_name = f"{self.session_prefix}-{agent_id}-{task_id[:8]}"
        launch = self._build_launch_command(agent_id=agent_id, prompt=prompt, session_id=session_id)
        await self._run_tmux(["new-session", "-d", "-s", session_name, launch])

        self._sessions[session_name] = TmuxSession(
            session_name=session_name,
            agent_id=agent_id,
            task_id=task_id,
            prompt=prompt,
            session_id=session_id,
        )
        return session_name

    async def capture(self, session_name: str, lines: int = 200) -> str:
        await self._touch(session_name)
        start = f"-{max(1, int(lines))}"
        out = await self._run_tmux(["capture-pane", "-pt", session_name, "-S", start])
        return out.strip()

    async def terminate(self, session_name: str) -> bool:
        if session_name not in self._sessions:
            return False
        await self._run_tmux(["kill-session", "-t", session_name])
        self._sessions.pop(session_name, None)
        return True

    async def evict_idle_sessions(self) -> List[str]:
        now = time.time()
        evicted: List[str] = []

        for session_name, info in list(self._sessions.items()):
            if (now - info.last_used_at) < self.idle_ttl_s:
                continue
            try:
                await self._run_tmux(["kill-session", "-t", session_name])
            except Exception:
                # If tmux already dropped the session we still clear local state.
                pass
            self._sessions.pop(session_name, None)
            evicted.append(session_name)

        return evicted

    def list_sessions(self) -> List[Dict[str, object]]:
        items = [asdict(item) for item in self._sessions.values()]
        items.sort(key=lambda item: float(item["created_at"]), reverse=True)
        return items

    async def _touch(self, session_name: str) -> None:
        info = self._sessions.get(session_name)
        if info:
            info.last_used_at = time.time()

    async def _ensure_tmux_available(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-V",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or "tmux not available"
            raise RuntimeError(detail)

    def _build_launch_command(self, *, agent_id: str, prompt: str, session_id: str) -> str:
        cmd = [self.command, *self.args, "-p", prompt]

        env = os.environ.copy()
        env.update(self.env)
        env["G3LOBSTER_AGENT_ID"] = agent_id
        env["G3LOBSTER_SESSION_ID"] = session_id

        exports = [f"{key}={shlex.quote(value)}" for key, value in env.items() if key.startswith("G3LOBSTER_")]
        command = " ".join(shlex.quote(part) for part in cmd)
        if exports:
            return f"{' '.join(exports)} {command}"
        return command

    async def _run_tmux(self, args: List[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or f"tmux exited with {proc.returncode}"
            raise RuntimeError(detail)
        return stdout.decode("utf-8", errors="replace")
