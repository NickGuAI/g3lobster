"""Async wrapper that runs Gemini CLI in headless (-p) mode per task."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Dict, Iterable, List, Optional


ALLOWED_MCP_SERVER_NAMES_FLAG = "--allowed-mcp-server-names"
PROMPT_FLAG = "-p"


def _normalize_timeout(timeout: Optional[float]) -> Optional[float]:
    if timeout is None:
        return None
    timeout_s = float(timeout)
    if timeout_s <= 0:
        return None
    return timeout_s


async def _wait_for_process_exit(
    proc: asyncio.subprocess.Process,
    timeout: Optional[float],
    stderr: Optional[asyncio.StreamReader],
) -> None:
    """Wait for process exit and surface non-zero exit codes with stderr."""
    if timeout is not None:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    else:
        await proc.wait()
    if proc.returncode == 0:
        return

    stderr_text = ""
    if stderr is not None:
        stderr_bytes = await stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

    detail = stderr_text or f"Gemini process exited with code {proc.returncode}"
    raise RuntimeError(detail)


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

    async def ask(self, prompt: str, timeout: Optional[float] = 120.0, session_id: Optional[str] = None) -> str:
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
                limit=30 * 1024 * 1024,
            )
            self._active_process = proc

            timeout_s = _normalize_timeout(timeout)
            try:
                if timeout_s is None:
                    stdout, _ = await proc.communicate()
                else:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
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

    async def ask_stream(self, prompt: str, timeout: Optional[float] = 120.0, session_id: Optional[str] = None):
        """Send a prompt and yield StreamEvent objects as they arrive.

        Spawns a fresh subprocess with --output-format stream-json and reads
        stdout line-by-line, yielding parsed StreamEvent objects.
        """
        if not self._ready:
            raise RuntimeError("GeminiProcess has not been initialised (call spawn first)")

        from g3lobster.cli.streaming import stream_events

        async with self._lock:
            cmd = [self.command] + self.args + ["--output-format", "stream-json", PROMPT_FLAG, prompt]
            if self._mcp_server_names and self._mcp_server_names != ["*"]:
                cmd.extend([ALLOWED_MCP_SERVER_NAMES_FLAG, *self._mcp_server_names])

            env = os.environ.copy()
            env.update(self.env)
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
                limit=30 * 1024 * 1024,
            )
            self._active_process = proc

            timeout_s = _normalize_timeout(timeout)
            loop = asyncio.get_running_loop()
            deadline = (loop.time() + timeout_s) if timeout_s is not None else None

            try:
                assert proc.stdout is not None
                async for event in stream_events(proc.stdout):
                    yield event
                    if event.is_terminal:
                        break

                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        proc.kill()
                        raise asyncio.TimeoutError("Stream read timed out")

                await _wait_for_process_exit(proc, remaining, proc.stderr)
            finally:
                self._active_process = None
                if proc.returncode is None:
                    proc.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(proc.wait(), timeout=5.0)

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


class TmuxSubagentProcess:
    """Manages a persistent Gemini CLI subprocess via tmux with stream-json output.

    Unlike GeminiProcess which spawns a new process per prompt, this class
    maintains a persistent tmux session that can receive multiple prompts.
    """

    def __init__(
        self,
        command: str,
        args: Optional[Iterable[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_prefix: str = "g3lobster",
    ):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.agent_id = agent_id
        self.session_prefix = session_prefix
        self._mcp_server_names: Optional[List[str]] = None
        self._ready = False
        self._session_name: Optional[str] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    @property
    def session_name(self) -> str:
        """Tmux session name for this subagent."""
        if self._session_name:
            return self._session_name
        agent_part = self.agent_id or "default"
        self._session_name = f"{self.session_prefix}-{agent_part}"
        return self._session_name

    async def spawn(self, mcp_server_names: Optional[List[str]] = None) -> None:
        """Initialize the tmux subagent process.

        Stores MCP config. The actual tmux session is created on first ask().
        """
        self._mcp_server_names = mcp_server_names
        self._ready = True

    def is_alive(self) -> bool:
        """Check if the subagent is ready to accept prompts."""
        return self._ready

    def _build_cmd(self, prompt: str) -> List[str]:
        """Build the Gemini CLI command with stream-json output."""
        cmd = [self.command] + self.args + [
            "--output-format", "stream-json",
            PROMPT_FLAG, prompt,
        ]
        if self._mcp_server_names and self._mcp_server_names != ["*"]:
            cmd.extend([ALLOWED_MCP_SERVER_NAMES_FLAG, *self._mcp_server_names])
        return cmd

    def _build_env(self, session_id: Optional[str] = None) -> Dict[str, str]:
        """Build environment variables for the subprocess."""
        env = os.environ.copy()
        env.update(self.env)
        if self.agent_id:
            env["G3LOBSTER_AGENT_ID"] = self.agent_id
        if session_id:
            env["G3LOBSTER_SESSION_ID"] = session_id
        return env

    async def ask(self, prompt: str, timeout: Optional[float] = 120.0, session_id: Optional[str] = None) -> str:
        """Send a prompt and collect the full response (blocking).

        This is the compatibility interface matching GeminiProcess.ask().
        Uses stream-json internally but accumulates the full response.
        """
        if not self._ready:
            raise RuntimeError("TmuxSubagentProcess has not been initialised (call spawn first)")

        from g3lobster.cli.streaming import StreamEventType, accumulate_text, parse_stream_event

        async with self._lock:
            cmd = self._build_cmd(prompt)
            env = self._build_env(session_id)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                limit=30 * 1024 * 1024,
            )

            events = []
            try:
                async def _read_events():
                    assert proc.stdout is not None
                    while True:
                        line_bytes = await proc.stdout.readline()
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").rstrip()
                        if not line:
                            continue
                        event = parse_stream_event(line)
                        events.append(event)

                timeout_s = _normalize_timeout(timeout)
                if timeout_s is None:
                    await _read_events()
                else:
                    await asyncio.wait_for(_read_events(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                raise
            finally:
                if proc.returncode is None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(proc.wait(), timeout=5.0)

            return accumulate_text(events)

    async def ask_stream(self, prompt: str, timeout: Optional[float] = 120.0, session_id: Optional[str] = None):
        """Send a prompt and yield StreamEvent objects as they arrive.

        This is the streaming interface for incremental output.
        Returns an async generator of StreamEvent objects.
        """
        if not self._ready:
            raise RuntimeError("TmuxSubagentProcess has not been initialised (call spawn first)")

        from g3lobster.cli.streaming import stream_events

        cmd = self._build_cmd(prompt)
        env = self._build_env(session_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=env,
            limit=30 * 1024 * 1024,
        )

        try:
            assert proc.stdout is not None
            timeout_s = _normalize_timeout(timeout)
            loop = asyncio.get_running_loop()
            deadline = (loop.time() + timeout_s) if timeout_s is not None else None
            async for event in stream_events(proc.stdout):
                yield event
                if event.is_terminal:
                    break

            if deadline is None:
                remaining = None
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    proc.kill()
                    raise asyncio.TimeoutError("Stream read timed out")

            await _wait_for_process_exit(proc, remaining, proc.stderr)
        finally:
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)

    async def kill(self) -> None:
        """Kill any active subprocess."""
        proc = self._process
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
        self._ready = False
