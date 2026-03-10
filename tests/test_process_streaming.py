from __future__ import annotations

import pytest

from g3lobster.cli.process import GeminiProcess
from g3lobster.cli.streaming import StreamEventType


@pytest.mark.asyncio
async def test_gemini_process_ask_stream_raises_on_non_zero_exit(tmp_path) -> None:
    script = tmp_path / "fake-gemini.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "while [ \"$#\" -gt 0 ]; do",
                "  case \"$1\" in",
                "    --output-format) shift 2 ;;",
                "    -p) shift 2 ;;",
                "    *) shift ;;",
                "  esac",
                "done",
                "printf '%s\\n' '{\"type\":\"message\",\"timestamp\":\"2026-03-10T00:00:00Z\",\"role\":\"assistant\",\"content\":\"partial\",\"delta\":true}'",
                "echo 'boom' >&2",
                "exit 1",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    process = GeminiProcess(command=str(script), cwd=str(tmp_path))
    await process.spawn()

    seen_event_types = []
    with pytest.raises(RuntimeError, match="boom"):
        async for event in process.ask_stream("hello"):
            seen_event_types.append(event.event_type)

    assert seen_event_types == [StreamEventType.MESSAGE]
