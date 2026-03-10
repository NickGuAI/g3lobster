from __future__ import annotations

from g3lobster.cli.streaming import StreamEventType, accumulate_text, parse_stream_event


def test_parse_stream_event_supports_gemini_message_chunks() -> None:
    event = parse_stream_event(
        '{"type":"message","timestamp":"2026-03-10T00:00:00Z","role":"assistant","content":"Hello","delta":true}'
    )

    assert event.event_type == StreamEventType.MESSAGE
    assert event.text == "Hello"


def test_accumulate_text_ignores_user_messages_and_collects_assistant_output() -> None:
    events = [
        parse_stream_event(
            '{"type":"message","timestamp":"2026-03-10T00:00:00Z","role":"user","content":"Prompt"}'
        ),
        parse_stream_event(
            '{"type":"message","timestamp":"2026-03-10T00:00:01Z","role":"assistant","content":"Hello","delta":true}'
        ),
        parse_stream_event(
            '{"type":"tool_use","timestamp":"2026-03-10T00:00:02Z","tool_name":"Read","tool_id":"tool-1","parameters":{}}'
        ),
        parse_stream_event(
            '{"type":"message","timestamp":"2026-03-10T00:00:03Z","role":"assistant","content":" world","delta":true}'
        ),
        parse_stream_event(
            '{"type":"result","timestamp":"2026-03-10T00:00:04Z","status":"success"}'
        ),
    ]

    assert accumulate_text(events) == "Hello world"
