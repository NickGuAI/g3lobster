"""Tests for thread summarizer with institutional context."""

from __future__ import annotations

import pytest

from g3lobster.chat.thread_summarizer import (
    CATCHUP_INTENT_RE,
    ThreadSummarizer,
    _build_summary_prompt,
    _extract_topics,
    _format_thread_for_prompt,
    detect_catchup_intent,
    extract_participants,
    fetch_thread_messages,
    search_related_memory,
)


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestDetectCatchupIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "catch me up",
            "Catch me up on this thread",
            "can you catch me up?",
            "summarize this thread",
            "summarize thread",
            "what did I miss",
            "What did I miss?",
            "tldr",
            "TLDR",
            "tl;dr",
            "TL;DR please",
            "what's been happening",
            "what is happening here",
            "what has been happening",
        ],
    )
    def test_positive_matches(self, text: str) -> None:
        assert detect_catchup_intent(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "can you help me?",
            "schedule a meeting",
            "what's the weather",
            "please do the task",
            "",
        ],
    )
    def test_negative_matches(self, text: str) -> None:
        assert detect_catchup_intent(text) is False


# ---------------------------------------------------------------------------
# Participant extraction
# ---------------------------------------------------------------------------

class TestExtractParticipants:
    def test_extracts_unique_participants(self) -> None:
        messages = [
            {"sender": {"name": "users/1", "displayName": "Alice", "type": "HUMAN"}},
            {"sender": {"name": "users/2", "displayName": "Bob", "type": "HUMAN"}},
            {"sender": {"name": "users/1", "displayName": "Alice", "type": "HUMAN"}},
            {"sender": {"name": "users/bot", "displayName": "Agent", "type": "BOT"}},
        ]
        participants = extract_participants(messages)
        assert len(participants) == 3
        names = {p["name"] for p in participants}
        assert names == {"users/1", "users/2", "users/bot"}

    def test_empty_messages(self) -> None:
        assert extract_participants([]) == []

    def test_missing_sender(self) -> None:
        messages = [{"text": "hello"}, {"sender": {}}]
        participants = extract_participants(messages)
        assert len(participants) == 0


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------

class TestExtractTopics:
    def test_extracts_frequent_words(self) -> None:
        messages = [
            {"text": "We need to discuss the deployment pipeline"},
            {"text": "The deployment is blocked by the pipeline issue"},
            {"text": "Let's fix the pipeline today"},
        ]
        topics = _extract_topics(messages)
        assert "pipeline" in topics
        assert "deployment" in topics

    def test_empty_messages(self) -> None:
        assert _extract_topics([]) == []


# ---------------------------------------------------------------------------
# Thread formatting
# ---------------------------------------------------------------------------

class TestFormatThread:
    def test_formats_messages(self) -> None:
        messages = [
            {
                "sender": {"displayName": "Alice"},
                "text": "Hello team",
                "createTime": "2025-01-15T10:00:00Z",
            },
            {
                "sender": {"displayName": "Bob"},
                "text": "Hi Alice",
                "createTime": "2025-01-15T10:01:00Z",
            },
        ]
        result = _format_thread_for_prompt(messages)
        assert "Alice: Hello team" in result
        assert "Bob: Hi Alice" in result

    def test_skips_empty_text(self) -> None:
        messages = [
            {"sender": {"displayName": "Alice"}, "text": "", "createTime": "2025-01-15T10:00:00Z"},
            {"sender": {"displayName": "Bob"}, "text": "Hi", "createTime": "2025-01-15T10:01:00Z"},
        ]
        result = _format_thread_for_prompt(messages)
        assert "Alice" not in result
        assert "Bob: Hi" in result

    def test_truncates_long_messages(self) -> None:
        messages = [
            {
                "sender": {"displayName": "Alice"},
                "text": "x" * 600,
                "createTime": "2025-01-15T10:00:00Z",
            },
        ]
        result = _format_thread_for_prompt(messages)
        assert len(result) < 600
        assert "..." in result


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestBuildSummaryPrompt:
    def test_basic_prompt(self) -> None:
        prompt = _build_summary_prompt("thread text here", [], [])
        assert "Key Points" in prompt
        assert "Decisions" in prompt
        assert "Action Items" in prompt
        assert "thread text here" in prompt

    def test_with_memory_hits(self) -> None:
        memory_hits = [
            {"source": "memory.md", "snippet": "discussed deployment last week", "memory_type": "memory"},
        ]
        prompt = _build_summary_prompt("thread", memory_hits, [])
        assert "Related Agent Memory" in prompt
        assert "discussed deployment last week" in prompt

    def test_with_email_hits(self) -> None:
        email_hits = [
            {"subject": "Deployment Plan", "from": "alice@example.com", "snippet": "Here is the plan"},
        ]
        prompt = _build_summary_prompt("thread", [], email_hits)
        assert "Related Email Threads" in prompt
        assert "Deployment Plan" in prompt


# ---------------------------------------------------------------------------
# Memory search integration
# ---------------------------------------------------------------------------

class FakeSearchHit:
    def __init__(self, snippet: str, source: str = "test.md", memory_type: str = "memory"):
        self.snippet = snippet
        self.source = source
        self.memory_type = memory_type


class FakeSearchEngine:
    def __init__(self, results=None):
        self._results = results or []
        self.queries: list[str] = []

    def search(self, query, *, agent_ids=None, limit=20, **kwargs):
        self.queries.append(query)
        return self._results


class TestSearchRelatedMemory:
    def test_searches_topics_and_participants(self) -> None:
        engine = FakeSearchEngine(results=[FakeSearchHit("found something")])
        participants = [{"display_name": "Alice", "type": "HUMAN"}]
        topics = ["deployment", "pipeline"]

        hits = search_related_memory(engine, participants, topics)
        assert len(hits) > 0
        assert hits[0]["snippet"] == "found something"
        # Should have searched for topics + participant name
        assert "deployment" in engine.queries
        assert "pipeline" in engine.queries
        assert "Alice" in engine.queries

    def test_deduplicates_results(self) -> None:
        hit = FakeSearchHit("same snippet")
        engine = FakeSearchEngine(results=[hit])
        participants = [{"display_name": "Alice", "type": "HUMAN"}]
        topics = ["deployment", "pipeline"]

        hits = search_related_memory(engine, participants, topics)
        # Even though multiple queries return the same hit, it should be deduplicated
        assert len(hits) == 1

    def test_empty_topics(self) -> None:
        engine = FakeSearchEngine()
        hits = search_related_memory(engine, [], [])
        assert hits == []


# ---------------------------------------------------------------------------
# Fetch thread messages (mocked)
# ---------------------------------------------------------------------------

class FakeCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeMessagesAPI:
    def __init__(self, responses):
        self._responses = list(responses)
        self._call_index = 0

    def list(self, **kwargs):
        resp = self._responses[self._call_index] if self._call_index < len(self._responses) else {"messages": []}
        self._call_index += 1
        return FakeCall(resp)


class FakeSpacesAPI:
    def __init__(self, messages_api):
        self._messages_api = messages_api

    def messages(self):
        return self._messages_api


class FakeService:
    def __init__(self, messages_api):
        self._spaces = FakeSpacesAPI(messages_api)

    def spaces(self):
        return self._spaces


class TestFetchThreadMessages:
    def test_fetches_single_page(self) -> None:
        messages_api = FakeMessagesAPI([
            {
                "messages": [
                    {"text": "hello", "createTime": "2025-01-15T10:00:00Z", "thread": {"name": "t1"}},
                    {"text": "world", "createTime": "2025-01-15T10:01:00Z", "thread": {"name": "t1"}},
                ],
            },
        ])
        service = FakeService(messages_api)
        result = fetch_thread_messages(service, "spaces/test", "t1")
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[1]["text"] == "world"

    def test_paginates(self) -> None:
        messages_api = FakeMessagesAPI([
            {"messages": [{"text": "p1", "createTime": "2025-01-15T10:00:00Z"}], "nextPageToken": "tok2"},
            {"messages": [{"text": "p2", "createTime": "2025-01-15T10:01:00Z"}]},
        ])
        service = FakeService(messages_api)
        result = fetch_thread_messages(service, "spaces/test", "t1")
        assert len(result) == 2

    def test_empty_thread(self) -> None:
        messages_api = FakeMessagesAPI([{"messages": []}])
        service = FakeService(messages_api)
        result = fetch_thread_messages(service, "spaces/test", "t1")
        assert result == []


# ---------------------------------------------------------------------------
# ThreadSummarizer integration (mocked Gemini)
# ---------------------------------------------------------------------------

class TestThreadSummarizerSummarize:
    def test_returns_no_messages(self) -> None:
        messages_api = FakeMessagesAPI([{"messages": []}])
        service = FakeService(messages_api)
        summarizer = ThreadSummarizer(service=service)
        result = summarizer.summarize("spaces/test", "t1")
        assert "No messages" in result

    def test_summarize_calls_gemini(self, monkeypatch) -> None:
        messages_api = FakeMessagesAPI([
            {
                "messages": [
                    {
                        "text": "Let's discuss the release",
                        "createTime": "2025-01-15T10:00:00Z",
                        "sender": {"name": "users/1", "displayName": "Alice", "type": "HUMAN"},
                    },
                ],
            },
        ])
        service = FakeService(messages_api)

        captured_prompts: list[str] = []

        def fake_call_gemini(prompt, **kwargs):
            captured_prompts.append(prompt)
            return "**Key Points**\n- Discussed release"

        import g3lobster.chat.thread_summarizer as ts_mod
        monkeypatch.setattr(ts_mod, "_call_gemini", fake_call_gemini)

        summarizer = ThreadSummarizer(service=service)
        result = summarizer.summarize("spaces/test", "t1")
        assert "Key Points" in result
        assert len(captured_prompts) == 1
        assert "release" in captured_prompts[0]
