from __future__ import annotations

import threading
import time

import pytest

from g3lobster.memory.compactor import CompactionEngine
from g3lobster.memory.manager import MemoryManager


def test_compactor_uses_count_precheck_before_loading_messages() -> None:
    class _SessionStore:
        def __init__(self) -> None:
            self.read_messages_called = False

        def message_count(self, _session_id: str) -> int:
            return 3

        def read_messages(self, _session_id: str):  # pragma: no cover - should not run
            self.read_messages_called = True
            raise AssertionError("read_messages should not be called when below threshold")

        def rewrite_session(self, _session_id: str, _entries):  # pragma: no cover - should not run
            raise AssertionError("rewrite_session should not be called when below threshold")

    class _ProcedureStore:
        def upsert_procedures(self, _procedures):  # pragma: no cover - should not run
            raise AssertionError("procedures should not be updated when below threshold")

    session_store = _SessionStore()
    compactor = CompactionEngine(
        session_store=session_store,
        procedure_store=_ProcedureStore(),
        compact_threshold=4,
    )

    compacted = compactor.maybe_compact("thread-1")
    assert compacted is False
    assert session_store.read_messages_called is False


def test_compactor_calls_gemini_cli_for_chunk_summaries(monkeypatch) -> None:
    class _SessionStore:
        def message_count(self, _session_id: str) -> int:
            return 4

        def read_messages(self, _session_id: str):
            return [
                {"type": "message", "message": {"role": "user", "content": "Deploy app"}},
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": "1. Check git status\n2. Run tests\n3. Deploy",
                    },
                },
                {"type": "message", "message": {"role": "user", "content": "Deploy app again"}},
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": "1. Check git status\n2. Run tests\n3. Deploy",
                    },
                },
            ]

        def rewrite_session(self, _session_id: str, entries):
            self.rewritten = entries

    class _ProcedureStore:
        def upsert_procedures(self, _procedures) -> None:
            pass

    class _Result:
        returncode = 0
        stdout = "- deployment routine confirmed\n- tests and deploy steps completed\n"
        stderr = ""

    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Result()

    monkeypatch.setattr("g3lobster.memory.compactor.subprocess.run", _fake_run)

    session_store = _SessionStore()
    compactor = CompactionEngine(
        session_store=session_store,
        procedure_store=_ProcedureStore(),
        compact_threshold=4,
        compact_keep_ratio=0.25,
        compact_chunk_size=2,
    )

    compacted = compactor.maybe_compact("thread-1")

    assert compacted is True
    assert len(calls) == 2
    assert calls[0][0][0] == "gemini"
    assert "-p" in calls[0][0]
    summary = session_store.rewritten[0]["summary"]
    assert "Chunk 1:" in summary
    assert "deployment routine confirmed" in summary


def test_auto_compaction_rewrites_session_and_keeps_recent_messages(tmp_path) -> None:
    memory = MemoryManager(
        data_dir=str(tmp_path / "agent"),
        summarize_threshold=100,
        compact_threshold=8,
        compact_keep_ratio=0.25,
        compact_chunk_size=3,
    )

    for index in range(8):
        role = "user" if index % 2 == 0 else "assistant"
        memory.append_message("thread-1", role, f"{role} message {index}")

    entries = memory.read_session("thread-1")
    assert entries
    assert entries[0]["type"] == "compaction"
    assert "summary" in entries[0]
    assert "Chunk 1" in entries[0]["summary"]

    kept_messages = [item for item in entries if item.get("type") == "message"]
    assert len(kept_messages) == 2
    assert kept_messages[0]["message"]["content"] == "user message 6"
    assert kept_messages[1]["message"]["content"] == "assistant message 7"
    assert memory.sessions.message_count("thread-1") == 2

    memory_text = memory.read_memory()
    assert "Compaction thread-1" in memory_text


def test_compaction_at_threshold_boundary(tmp_path) -> None:
    memory = MemoryManager(
        data_dir=str(tmp_path / "agent"),
        compact_threshold=4,
        compact_keep_ratio=0.25,
    )

    for index in range(4):
        role = "user" if index % 2 == 0 else "assistant"
        memory.append_message("boundary-thread", role, f"{role} message {index}")

    entries = memory.read_session("boundary-thread")
    assert entries[0]["type"] == "compaction"

    memory_text = memory.read_memory()
    assert "Compaction boundary-thread" in memory_text


def test_rewrite_failure_does_not_flush_to_memory(tmp_path, monkeypatch) -> None:
    """When rewrite_session fails, the after_compact callback must NOT have run."""
    memory = MemoryManager(
        data_dir=str(tmp_path / "agent"),
        summarize_threshold=100,
        compact_threshold=2,
        compact_keep_ratio=0.5,
    )
    memory.append_message("broken-thread", "user", "remember this preference for future runs")

    def _rewrite_fail(_session_id: str, _entries) -> None:
        raise OSError("simulated rewrite failure")

    monkeypatch.setattr(memory.sessions, "rewrite_session", _rewrite_fail)

    with pytest.raises(OSError):
        memory.append_message("broken-thread", "assistant", "ack")

    memory_text = memory.read_memory()
    assert "Compaction broken-thread" not in memory_text

    entries = memory.read_session("broken-thread")
    assert [entry.get("type") for entry in entries] == ["message", "message"]


def test_flush_failure_after_rewrite_preserves_compacted_session(tmp_path, monkeypatch) -> None:
    """If the after_compact flush to MEMORY.md fails, compaction still persists
    and the exception does not propagate to the caller."""
    memory = MemoryManager(
        data_dir=str(tmp_path / "agent"),
        summarize_threshold=100,
        compact_threshold=2,
        compact_keep_ratio=0.5,
    )
    memory.append_message("flush-thread", "user", "remember this preference")

    def _flush_fail(_section_title: str, _content: str) -> None:
        raise OSError("simulated flush failure")

    monkeypatch.setattr(memory, "append_memory_section", _flush_fail)

    # The callback failure is caught inside compactor; no exception propagates.
    memory.append_message("flush-thread", "assistant", "ack")

    entries = memory.read_session("flush-thread")
    assert entries[0]["type"] == "compaction"


def test_compaction_ingests_candidates_into_store(tmp_path) -> None:
    memory = MemoryManager(
        data_dir=str(tmp_path / "agent"),
        compact_threshold=8,
        compact_keep_ratio=0.25,
        compact_chunk_size=4,
        procedure_min_frequency=3,
    )

    assistant_steps = "\n".join(
        [
            "1. Check git status for uncommitted changes",
            "2. Run test suite",
            "3. Build docker image with current tag",
            "4. Push image to registry",
            "5. Verify deployment health check",
        ]
    )

    for _turn in range(4):
        memory.append_message("deploy-thread", "user", "Deploy the app to production now")
        memory.append_message("deploy-thread", "assistant", assistant_steps)

    # Candidates are ingested into the candidate store, not PROCEDURES.md directly.
    candidates = memory.candidate_store.list_all()
    assert len(candidates) >= 1
    deploy_candidates = [c for c in candidates if "deploy" in c.trigger]
    assert deploy_candidates
    assert deploy_candidates[0].weight >= 1.0
    assert "Build docker image with current tag" in deploy_candidates[0].steps


def test_compaction_rewrite_does_not_drop_concurrent_append(tmp_path, monkeypatch) -> None:
    memory = MemoryManager(
        data_dir=str(tmp_path / "agent"),
        summarize_threshold=100,
        compact_threshold=2,
        compact_keep_ratio=0.5,
    )
    memory.append_message("race-thread", "user", "first message")

    summarize_started = threading.Event()
    summarize_release = threading.Event()
    errors: list[Exception] = []
    late_append_done = threading.Event()

    def _blocking_summary(_messages):
        summarize_started.set()
        if not summarize_release.wait(timeout=3):
            raise TimeoutError("timed out waiting for summary release")
        return "- compacted summary"

    monkeypatch.setattr(memory.compactor, "_summarize_messages", _blocking_summary)

    def _compact_trigger() -> None:
        try:
            memory.append_message("race-thread", "assistant", "second message")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    def _late_append() -> None:
        try:
            memory.append_message("race-thread", "user", "late message")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)
        finally:
            late_append_done.set()

    trigger_thread = threading.Thread(target=_compact_trigger)
    trigger_thread.start()
    assert summarize_started.wait(timeout=3)

    late_thread = threading.Thread(target=_late_append)
    late_thread.start()
    time.sleep(0.1)
    assert late_append_done.is_set() is False

    summarize_release.set()
    trigger_thread.join(timeout=3)
    late_thread.join(timeout=3)

    assert not errors
    assert not trigger_thread.is_alive()
    assert not late_thread.is_alive()

    entries = memory.read_session("race-thread")
    assert entries[0]["type"] == "compaction"

    messages = [entry for entry in entries if entry.get("type") == "message"]
    contents = [entry["message"]["content"] for entry in messages]
    assert contents
    assert contents[-1] == "late message"
