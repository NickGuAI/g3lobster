from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from g3lobster.cli.process import ALLOWED_MCP_SERVER_NAMES_FLAG, PROMPT_FLAG, GeminiProcess


@pytest.mark.asyncio
async def test_gemini_process_roundtrip(tmp_path: Path) -> None:
    """Each ask() spawns a fresh process with -p and returns stdout."""
    script = tmp_path / "echo.py"
    script.write_text(
        "import sys; print(f'OUT:{sys.argv[sys.argv.index(\"-p\")+1]}')\n",
        encoding="utf-8",
    )

    proc = GeminiProcess(command="python3", args=[str(script)])
    await proc.spawn()

    response = await proc.ask("hello", timeout=5.0)
    assert response == "OUT:hello"
    assert proc.is_alive()


@pytest.mark.asyncio
async def test_ask_includes_mcp_server_names(monkeypatch) -> None:
    captured = {}

    class DummyProcess:
        returncode = 0
        async def communicate(self):
            return (b"ok", b"")
        async def wait(self):
            pass

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    proc = GeminiProcess(command="gemini", args=["-y"])
    await proc.spawn(mcp_server_names=["gmail", "calendar"])

    await proc.ask("test prompt", timeout=5.0)

    assert captured["cmd"] == [
        "gemini",
        "-y",
        PROMPT_FLAG,
        "test prompt",
        ALLOWED_MCP_SERVER_NAMES_FLAG,
        "gmail",
        "calendar",
    ]


@pytest.mark.asyncio
async def test_ask_skips_mcp_flag_for_all_servers(monkeypatch) -> None:
    captured = {}

    class DummyProcess:
        returncode = 0
        async def communicate(self):
            return (b"ok", b"")
        async def wait(self):
            pass

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    proc = GeminiProcess(command="gemini", args=["-y"])
    await proc.spawn(mcp_server_names=["*"])

    await proc.ask("test prompt", timeout=5.0)

    assert captured["cmd"] == ["gemini", "-y", PROMPT_FLAG, "test prompt"]


@pytest.mark.asyncio
async def test_ask_raises_on_nonzero_exit(monkeypatch) -> None:
    class DummyProcess:
        returncode = 1
        async def communicate(self):
            return (b"", b"error")
        async def wait(self):
            pass

    async def fake_exec(*cmd, **kwargs):
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    proc = GeminiProcess(command="gemini", args=["-y"])
    await proc.spawn()

    with pytest.raises(RuntimeError, match="exited with code 1"):
        await proc.ask("fail", timeout=5.0)


@pytest.mark.asyncio
async def test_is_alive_false_before_spawn() -> None:
    proc = GeminiProcess(command="gemini")
    assert not proc.is_alive()
