"""Async helpers for managing isolated tmux sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class TmuxCommandResult:
    returncode: int
    stdout: str
    stderr: str


class TmuxSession:
    """Lifecycle wrapper around one tmux session."""

    def __init__(self, name: str, socket_name: str = "g3lobster"):
        self.name = str(name)
        self.socket_name = str(socket_name)

    async def _run(self, *args: str, check: bool = True) -> TmuxCommandResult:
        cmd = ["tmux", "-L", self.socket_name, *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("tmux is not installed or not available in PATH") from exc

        stdout_bytes, stderr_bytes = await proc.communicate()
        result = TmuxCommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace").strip(),
            stderr=stderr_bytes.decode("utf-8", errors="replace").strip(),
        )
        if check and result.returncode != 0:
            detail = result.stderr or result.stdout or f"tmux command failed: {' '.join(args)}"
            raise RuntimeError(detail)
        return result

    async def exists(self) -> bool:
        result = await self._run("has-session", "-t", self.name, check=False)
        return result.returncode == 0

    async def create(self, command: str, cwd: Optional[str] = None) -> None:
        args = ["new-session", "-d", "-s", self.name]
        if cwd:
            args.extend(["-c", cwd])
        args.append(command)
        await self._run(*args, check=True)

    async def send_keys(self, text: str) -> None:
        await self._run("send-keys", "-t", self.name, text, "C-m", check=True)

    async def capture_pane(self, max_lines: int = 2000) -> str:
        start = f"-{max(10, int(max_lines))}"
        result = await self._run(
            "capture-pane",
            "-p",
            "-t",
            self.name,
            "-S",
            start,
            check=True,
        )
        return result.stdout

    async def kill(self) -> None:
        await self._run("kill-session", "-t", self.name, check=False)
