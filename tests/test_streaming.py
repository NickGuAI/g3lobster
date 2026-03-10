"""Tests for streaming event parser and GeminiProcess.ask_stream."""

from __future__ import annotations

import asyncio
import json

import pytest

from g3lobster.cli.streaming import StreamEvent, StreamEventType, parse_stream_event, stream_events


def test_parse_tool_use_event() -> None:
    line = json.dumps({"toolCall": {"name": "search_gmail"}})
    event = parse_stream_event(line)
    assert event is not None
    assert event.type == StreamEventType.TOOL_USE
    assert event.tool_name == "search_gmail"


def test_parse_text_delta_event() -> None:
    line = json.dumps({"text": "Hello world"})
    event = parse_stream_event(line)
    assert event is not None
    assert event.type == StreamEventType.TEXT_DELTA
    assert event.text == "Hello world"


def test_parse_result_event() -> None:
    line = json.dumps({"result": "final answer"})
    event = parse_stream_event(line)
    assert event is not None
    assert event.type == StreamEventType.RESULT
    assert event.text == "final answer"


def test_parse_error_event() -> None:
    line = json.dumps({"error": "something broke"})
    event = parse_stream_event(line)
    assert event is not None
    assert event.type == StreamEventType.ERROR
    assert event.text == "something broke"


def test_parse_turn_end_event() -> None:
    line = json.dumps({"turnComplete": True})
    event = parse_stream_event(line)
    assert event is not None
    assert event.type == StreamEventType.TURN_END


def test_parse_empty_line_returns_none() -> None:
    assert parse_stream_event("") is None
    assert parse_stream_event("  ") is None


def test_parse_invalid_json_returns_none() -> None:
    assert parse_stream_event("not json") is None


def test_parse_non_dict_json_returns_none() -> None:
    assert parse_stream_event("[1,2,3]") is None


@pytest.mark.asyncio
async def test_stream_events_yields_events() -> None:
    lines = [
        json.dumps({"toolCall": {"name": "gmail_send"}}).encode() + b"\n",
        json.dumps({"text": "Sending email..."}).encode() + b"\n",
        json.dumps({"result": "Email sent"}).encode() + b"\n",
    ]

    async def line_iter():
        for line in lines:
            yield line

    events = []
    async for event in stream_events(line_iter()):
        events.append(event)

    types = [e.type for e in events]
    assert StreamEventType.TOOL_USE in types
    assert StreamEventType.TEXT_DELTA in types
    assert StreamEventType.RESULT in types


@pytest.mark.asyncio
async def test_stream_events_emits_result_from_accumulated_text() -> None:
    """When no explicit RESULT event is emitted, accumulated text becomes the result."""
    lines = [
        json.dumps({"text": "part1 "}).encode() + b"\n",
        json.dumps({"text": "part2"}).encode() + b"\n",
    ]

    async def line_iter():
        for line in lines:
            yield line

    events = []
    async for event in stream_events(line_iter()):
        events.append(event)

    # Should have 2 text deltas + 1 accumulated result
    result_events = [e for e in events if e.type == StreamEventType.RESULT]
    assert len(result_events) == 1
    assert result_events[0].text == "part1 part2"


@pytest.mark.asyncio
async def test_ask_stream_includes_output_format_flag(monkeypatch) -> None:
    """ask_stream passes --output-format stream-json to the subprocess."""
    from g3lobster.cli.process import GeminiProcess

    captured = {}

    class DummyProcess:
        returncode = 0

        def __init__(self):
            self.stdout = None
            self.stderr = None

        async def wait(self):
            pass

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)

        proc = DummyProcess()

        class FakeStdout:
            async def readline(self):
                return b""

        proc.stdout = FakeStdout()
        proc.stderr = asyncio.subprocess.PIPE
        proc.returncode = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    proc = GeminiProcess(command="gemini", args=["-y"])
    await proc.spawn(mcp_server_names=["gmail"])

    events = []
    async for event in proc.ask_stream("test prompt", timeout=5.0):
        events.append(event)

    assert "--output-format" in captured["cmd"]
    assert "stream-json" in captured["cmd"]
    assert "-p" in captured["cmd"]
    assert "test prompt" in captured["cmd"]
