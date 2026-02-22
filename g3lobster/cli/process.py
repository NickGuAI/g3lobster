"""Async wrapper that runs Gemini CLI in headless (-p) mode per task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Callable, Dict, Iterable, List, Optional


ALLOWED_MCP_SERVER_NAMES_FLAG = "--allowed-mcp-server-names"
PROMPT_FLAG = "-p"
logger = logging.getLogger(__name__)

ProcessEventHook = Callable[[str, dict], None]


class GeminiProcess:
    """Spawns a fresh Gemini CLI process for each prompt using ``-p``."""

    def __init__(
        self,
        command: str,
        args: Optional[Iterable[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        idle_read_window_s: float = 0.6,
    ):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.idle_read_window_s = idle_read_window_s
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

    @staticmethod
    def _emit_event(event_hook: Optional[ProcessEventHook], event_type: str, data: dict) -> None:
        if not event_hook:
            return
        try:
            event_hook(event_type, data)
        except Exception:
            logger.exception("Gemini process event hook failed")

    async def _read_stream(self, stream, chunks: List[bytes]) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)

    async def ask(
        self,
        prompt: str,
        timeout: float = 120.0,
        event_hook: Optional[ProcessEventHook] = None,
    ) -> str:
        if not self._ready:
            raise RuntimeError("GeminiProcess has not been initialised (call spawn first)")

        async with self._lock:
            cmd = [self.command] + self.args + [PROMPT_FLAG, prompt]
            if self._mcp_server_names and self._mcp_server_names != ["*"]:
                cmd.extend([ALLOWED_MCP_SERVER_NAMES_FLAG, *self._mcp_server_names])

            env = os.environ.copy()
            env.update(self.env)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
            self._active_process = proc
            started_at = time.time()
            self._emit_event(
                event_hook,
                "gemini.process.spawned",
                {
                    "pid": proc.pid,
                    "cmd_args": cmd,
                    "mcp_servers": list(self._mcp_server_names or []),
                },
            )

            stdout_chunks: List[bytes] = []
            stderr_chunks: List[bytes] = []
            stdout_task = asyncio.create_task(self._read_stream(proc.stdout, stdout_chunks))
            stderr_task = asyncio.create_task(self._read_stream(proc.stderr, stderr_chunks))
            timed_out = False
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
            finally:
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                self._active_process = None

            stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            elapsed = time.time() - started_at

            if timed_out:
                self._emit_event(
                    event_hook,
                    "gemini.process.timeout",
                    {
                        "pid": proc.pid,
                        "timeout_s": timeout,
                        "partial_output_length": len(stdout),
                    },
                )
                raise asyncio.TimeoutError()

            self._emit_event(
                event_hook,
                "gemini.process.completed",
                {
                    "pid": proc.pid,
                    "exit_code": proc.returncode,
                    "duration_s": elapsed,
                    "stdout_length": len(stdout),
                    "stderr_length": len(stderr),
                },
            )

            if proc.returncode != 0:
                self._emit_event(
                    event_hook,
                    "gemini.process.error",
                    {
                        "pid": proc.pid,
                        "exit_code": proc.returncode,
                        "stderr_snippet": stderr[:200],
                    },
                )
                raise RuntimeError(
                    f"Gemini process exited with code {proc.returncode}"
                )

            return stdout.strip()

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
