"""Async wrapper that runs Gemini CLI in headless (-p) mode per task."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import AsyncIterator, Dict, Iterable, List, Optional

from g3lobster.cli.streaming import StreamEvent, stream_events


ALLOWED_MCP_SERVER_NAMES_FLAG = "--allowed-mcp-server-names"
STREAM_OUTPUT_FLAG = "--output-format"
STREAM_OUTPUT_VALUE = "stream-json"
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

    async def ask(self, prompt: str, timeout: float = 120.0) -> str:
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

    async def ask_stream(self, prompt: str, timeout: float = 120.0) -> AsyncIterator[StreamEvent]:
        """Spawn a Gemini CLI process with stream-json output and yield events."""
        if not self._ready:
            raise RuntimeError("GeminiProcess has not been initialised (call spawn first)")

        async with self._lock:
            cmd = [self.command] + self.args + [
                STREAM_OUTPUT_FLAG, STREAM_OUTPUT_VALUE,
                PROMPT_FLAG, prompt,
            ]
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

            try:
                async def _read_lines() -> AsyncIterator[bytes]:
                    assert proc.stdout is not None
                    while True:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                        if not line:
                            break
                        yield line

                async for event in stream_events(_read_lines()):
                    yield event

                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                raise
            finally:
                self._active_process = None

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
