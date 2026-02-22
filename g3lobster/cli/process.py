"""Async wrapper that runs Gemini CLI in headless (-p) mode per task."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Dict, Iterable, List, Optional


ALLOWED_MCP_SERVER_NAMES_FLAG = "--allowed-mcp-server-names"
PROMPT_FLAG = "-p"


class GeminiProcess:
    """Spawns a fresh Gemini CLI process for each prompt using ``-p``."""

    def __init__(
        self,
        command: str,
        args: Optional[Iterable[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        idle_read_window_s: float = 0.6,
        agent_id: Optional[str] = None,
    ):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.idle_read_window_s = idle_read_window_s
        self.agent_id = agent_id
        self._mcp_server_names: Optional[List[str]] = None
        self._ready = False
        self._active_process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    async def spawn(self, mcp_server_names: Optional[List[str]] = None) -> None:
        """Store MCP config.  No persistent process is started."""
        self._mcp_server_names = mcp_server_names
        self._ready = True

    def is_alive(self) -> bool:
        return self._ready

    async def ask(self, prompt: str, timeout: float = 120.0, session_id: Optional[str] = None) -> str:
        if not self._ready:
            raise RuntimeError("GeminiProcess has not been initialised (call spawn first)")

        async with self._lock:
            cmd = [self.command] + self.args + [PROMPT_FLAG, prompt]
            if self._mcp_server_names and self._mcp_server_names != ["*"]:
                cmd.extend([ALLOWED_MCP_SERVER_NAMES_FLAG, *self._mcp_server_names])

            env = os.environ.copy()
            env.update(self.env)

            # Inject agent identity so MCP tools (e.g. delegation) know the caller
            if self.agent_id:
                env["G3LOBSTER_AGENT_ID"] = self.agent_id
            if session_id:
                env["G3LOBSTER_SESSION_ID"] = session_id

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
            self._active_process = proc

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                raise
            finally:
                self._active_process = None

            if proc.returncode != 0:
                raise RuntimeError(
                    f"Gemini process exited with code {proc.returncode}"
                )

            return stdout.decode("utf-8", errors="replace").strip()

    async def kill(self) -> None:
        proc = self._active_process
        if not proc or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
