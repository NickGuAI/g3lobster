from __future__ import annotations

import time

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.calendar.buffer import BufferedMessage, MessageBuffer
from g3lobster.calendar.checker import FocusEvent, FocusTimeChecker
from g3lobster.chat.bridge import ChatBridge
from g3lobster.tasks.types import TaskStatus


# ---------------------------------------------------------------------------
# Fake calendar service for FocusTimeChecker tests
# ---------------------------------------------------------------------------

class FakeEventsAPI:
    def __init__(self, items=None):
        self._items = items or []

    def list(self, **kwargs):
        class Result:
            def execute(inner_self):
                return {"items": self._items}
        return Result()


class FakeCalendarService:
    def __init__(self, items=None):
        self._events_api = FakeEventsAPI(items)

    def events(self):
        return self._events_api


# ---------------------------------------------------------------------------
# Fake chat service (reused from test_chat.py pattern)
# ---------------------------------------------------------------------------

class FakeCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeMessagesAPI:
    def __init__(self):
        self.created = []
        self.updated = []

    def list(self, parent, pageSize, orderBy):
        return FakeCall({"messages": []})

    def create(self, parent, body):
        self.created.append({"parent": parent, "body": body})
        return FakeCall({"name": "spaces/test/messages/1"})

    def update(self, name, updateMask, body):
        self.updated.append({"name": name, "updateMask": updateMask, "body": body})
        return FakeCall({"name": name})


class FakeSpacesAPI:
    def __init__(self, messages_api):
        self._messages_api = messages_api

    def messages(self):
        return self._messages_api

    def setup(self, body):
        return FakeCall({"name": "spaces/test"})


class FakeService:
    def __init__(self):
        self.messages_api = FakeMessagesAPI()
        self.spaces_api = FakeSpacesAPI(self.messages_api)

    def spaces(self):
        return self.spaces_api


class FakeRuntimeAgent:
    def __init__(self, persona):
        self.persona = persona

    async def assign(self, task):
        task.status = TaskStatus.COMPLETED
        task.result = "reply"
        return task

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        result_task = await self.assign(task)
        yield StreamEvent(
            event_type=StreamEventType.MESSAGE,
            data={"role": "assistant", "content": result_task.result or "", "delta": True},
        )
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            data={"status": "success"},
        )


class FakeRegistry:
    def __init__(self, data_dir, persona):
        self.data_dir = data_dir
        self.runtime = FakeRuntimeAgent(persona)

    def get_agent(self, agent_id):
        if agent_id == self.runtime.persona.id:
            return self.runtime
        return None

    def list_enabled_personas(self):
        return [self.runtime.persona]

    async def start_agent(self, agent_id):
        return agent_id == self.runtime.persona.id


# ---------------------------------------------------------------------------
# FocusTimeChecker tests
# ---------------------------------------------------------------------------

class TestFocusTimeCheckerDetection:
    """Test focus time detection: active, inactive, OOO."""

    def test_detects_focus_time_event(self):
        items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
                "status": "confirmed",
                "transparency": "opaque",
            }
        ]
        checker = FocusTimeChecker(FakeCalendarService(items))
        event = checker.check_focus_time("user@example.com")

        assert event is not None
        assert event.event_type == "focus"
        assert event.summary == "Focus Time"

    def test_detects_ooo_event(self):
        items = [
            {
                "eventType": "outOfOffice",
                "summary": "Vacation",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
                "status": "confirmed",
                "transparency": "opaque",
            }
        ]
        checker = FocusTimeChecker(FakeCalendarService(items))
        event = checker.check_focus_time("user@example.com")

        assert event is not None
        assert event.event_type == "ooo"
        assert event.summary == "Vacation"

    def test_detects_tentative_as_ooo(self):
        items = [
            {
                "eventType": "default",
                "summary": "Tentative block",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
                "status": "tentative",
                "transparency": "opaque",
            }
        ]
        checker = FocusTimeChecker(FakeCalendarService(items))
        event = checker.check_focus_time("user@example.com")

        assert event is not None
        assert event.event_type == "ooo"

    def test_returns_none_when_no_events(self):
        checker = FocusTimeChecker(FakeCalendarService([]))
        event = checker.check_focus_time("user@example.com")

        assert event is None

    def test_is_in_focus_time_true(self):
        items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
            }
        ]
        checker = FocusTimeChecker(FakeCalendarService(items))

        assert checker.is_in_focus_time("user@example.com") is True

    def test_is_in_focus_time_false(self):
        checker = FocusTimeChecker(FakeCalendarService([]))

        assert checker.is_in_focus_time("user@example.com") is False

    def test_detects_opaque_focus_time_by_summary(self):
        items = [
            {
                "eventType": "default",
                "summary": "Do Not Disturb",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
                "status": "confirmed",
                "transparency": "opaque",
            }
        ]
        checker = FocusTimeChecker(FakeCalendarService(items))
        event = checker.check_focus_time("user@example.com")

        assert event is not None
        assert event.event_type == "focus"
        assert event.summary == "Do Not Disturb"

    def test_ignores_regular_events(self):
        items = [
            {
                "eventType": "default",
                "summary": "Team standup",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
                "status": "confirmed",
                "transparency": "opaque",
            }
        ]
        checker = FocusTimeChecker(FakeCalendarService(items))
        event = checker.check_focus_time("user@example.com")

        assert event is None


class TestFocusTimeCheckerCache:
    """Test caching behavior and TTL."""

    def test_cache_returns_same_result_within_ttl(self):
        call_count = 0

        class CountingEventsAPI:
            def list(self, **kwargs):
                nonlocal call_count
                call_count += 1
                class Result:
                    def execute(inner_self):
                        return {"items": [
                            {
                                "eventType": "focusTime",
                                "summary": "Focus Time",
                                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
                            }
                        ]}
                return Result()

        class CountingService:
            def events(self):
                return CountingEventsAPI()

        checker = FocusTimeChecker(CountingService(), ttl_s=300.0)

        result1 = checker.check_focus_time("user@example.com")
        result2 = checker.check_focus_time("user@example.com")

        assert result1 is not None
        assert result2 is not None
        assert call_count == 1  # Only one API call due to cache

    def test_cache_expires_after_ttl(self):
        call_count = 0

        class CountingEventsAPI:
            def list(self, **kwargs):
                nonlocal call_count
                call_count += 1
                class Result:
                    def execute(inner_self):
                        return {"items": []}
                return Result()

        class CountingService:
            def events(self):
                return CountingEventsAPI()

        checker = FocusTimeChecker(CountingService(), ttl_s=0.0)

        checker.check_focus_time("user@example.com")
        checker.check_focus_time("user@example.com")

        assert call_count == 2  # TTL=0 means every call fetches

    def test_different_users_cached_independently(self):
        checker = FocusTimeChecker(FakeCalendarService([
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
            }
        ]), ttl_s=300.0)

        result_a = checker.check_focus_time("alice@example.com")
        result_b = checker.check_focus_time("bob@example.com")

        # Both should get results (both hit the same fake service)
        assert result_a is not None
        assert result_b is not None


class TestFocusTimeCheckerRefresh:
    """Test refresh() detecting focus end and calling callback."""

    def test_refresh_fires_on_focus_end_callback(self):
        ended_emails = []

        def on_end(email):
            ended_emails.append(email)

        # Start with focus events active
        focus_items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
            }
        ]
        service = FakeCalendarService(focus_items)
        checker = FocusTimeChecker(service, ttl_s=0, on_focus_end=on_end)

        # Prime the cache with active focus
        checker.check_focus_time("user@example.com")
        assert checker.is_in_focus_time("user@example.com")

        # Now change the service to return no events (focus ended)
        service._events_api._items = []

        checker.refresh()

        assert "user@example.com" in ended_emails

    def test_refresh_does_not_fire_callback_when_still_focused(self):
        ended_emails = []

        def on_end(email):
            ended_emails.append(email)

        focus_items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
            }
        ]
        checker = FocusTimeChecker(
            FakeCalendarService(focus_items), ttl_s=0, on_focus_end=on_end
        )

        checker.check_focus_time("user@example.com")
        checker.refresh()

        assert ended_emails == []

    def test_refresh_does_not_fire_callback_when_never_focused(self):
        ended_emails = []

        def on_end(email):
            ended_emails.append(email)

        checker = FocusTimeChecker(
            FakeCalendarService([]), ttl_s=0, on_focus_end=on_end
        )

        checker.check_focus_time("user@example.com")
        checker.refresh()

        assert ended_emails == []

    def test_refresh_handles_callback_exception_gracefully(self):
        def on_end(email):
            raise RuntimeError("boom")

        focus_items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T23:59:59+00:00"},
            }
        ]
        service = FakeCalendarService(focus_items)
        checker = FocusTimeChecker(service, ttl_s=0, on_focus_end=on_end)

        checker.check_focus_time("user@example.com")
        service._events_api._items = []

        # Should not raise
        checker.refresh()


# ---------------------------------------------------------------------------
# MessageBuffer tests
# ---------------------------------------------------------------------------

class TestMessageBuffer:
    """Test add, drain, persistence, and empty state."""

    def test_add_and_drain(self, tmp_path):
        buf = MessageBuffer(str(tmp_path))
        msg = BufferedMessage(
            sender_name="users/123",
            text="Hello",
            thread_id="spaces/test/threads/abc",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        buf.add("agent-1", msg)
        messages = buf.drain("agent-1")

        assert len(messages) == 1
        assert messages[0].sender_name == "users/123"
        assert messages[0].text == "Hello"

    def test_drain_clears_buffer(self, tmp_path):
        buf = MessageBuffer(str(tmp_path))
        msg = BufferedMessage(
            sender_name="users/123",
            text="Hello",
            thread_id="t1",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        buf.add("agent-1", msg)
        buf.drain("agent-1")

        # Second drain should return empty
        assert buf.drain("agent-1") == []

    def test_drain_empty_buffer(self, tmp_path):
        buf = MessageBuffer(str(tmp_path))
        assert buf.drain("agent-1") == []

    def test_has_messages(self, tmp_path):
        buf = MessageBuffer(str(tmp_path))
        assert buf.has_messages("agent-1") is False

        buf.add("agent-1", BufferedMessage(
            sender_name="u", text="hi", thread_id="t", timestamp="ts",
        ))
        assert buf.has_messages("agent-1") is True

    def test_multiple_messages(self, tmp_path):
        buf = MessageBuffer(str(tmp_path))

        for i in range(3):
            buf.add("agent-1", BufferedMessage(
                sender_name=f"users/{i}",
                text=f"msg {i}",
                thread_id="t1",
                timestamp=f"2026-01-01T00:0{i}:00+00:00",
            ))

        messages = buf.drain("agent-1")
        assert len(messages) == 3
        assert messages[0].text == "msg 0"
        assert messages[2].text == "msg 2"

    def test_persistence_survives_new_instance(self, tmp_path):
        data_dir = str(tmp_path)
        buf1 = MessageBuffer(data_dir)
        buf1.add("agent-1", BufferedMessage(
            sender_name="users/1", text="persisted", thread_id="t1", timestamp="ts1",
        ))

        # Create a new instance pointing to the same directory
        buf2 = MessageBuffer(data_dir)
        messages = buf2.drain("agent-1")

        assert len(messages) == 1
        assert messages[0].text == "persisted"

    def test_separate_agents_independent(self, tmp_path):
        buf = MessageBuffer(str(tmp_path))
        buf.add("agent-a", BufferedMessage(
            sender_name="u", text="for a", thread_id="t", timestamp="ts",
        ))
        buf.add("agent-b", BufferedMessage(
            sender_name="u", text="for b", thread_id="t", timestamp="ts",
        ))

        msgs_a = buf.drain("agent-a")
        msgs_b = buf.drain("agent-b")

        assert len(msgs_a) == 1
        assert msgs_a[0].text == "for a"
        assert len(msgs_b) == 1
        assert msgs_b[0].text == "for b"


# ---------------------------------------------------------------------------
# Bridge integration tests
# ---------------------------------------------------------------------------

def _make_persona(data_dir: str) -> AgentPersona:
    return save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="*",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )


def _make_message(text="Hello there", sender_name="users/123", bot_user_id="users/999"):
    return {
        "text": text,
        "sender": {"type": "HUMAN", "name": sender_name, "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": bot_user_id}},
            }
        ],
    }


class TestBridgeFocusIntercept:
    """Bridge integration: message interception during focus time."""

    @pytest.mark.asyncio
    async def test_message_intercepted_during_focus_time(self, tmp_path):
        data_dir = str(tmp_path / "data")
        persona = _make_persona(data_dir)

        service = FakeService()
        registry = FakeRegistry(data_dir, persona)

        focus_items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T17:00:00+00:00"},
            }
        ]
        focus_checker = FocusTimeChecker(FakeCalendarService(focus_items))
        message_buffer = MessageBuffer(str(tmp_path / "buffer"))

        bridge = ChatBridge(
            registry=registry,
            space_id="spaces/test",
            service=service,
            spaces_config=str(tmp_path / "spaces.json"),
            focus_checker=focus_checker,
            message_buffer=message_buffer,
        )

        await bridge.handle_message(_make_message())

        # Message should be buffered, not processed normally
        buffered = message_buffer.drain("luna")
        assert len(buffered) == 1
        assert buffered[0].text == "Hello there"

        # The bridge should send an interception notice, not a thinking message
        assert len(service.messages_api.created) == 1
        created_text = service.messages_api.created[0]["body"]["text"]
        assert "Focus Time" in created_text
        assert "saved" in created_text.lower() or "deliver" in created_text.lower()

        # No update calls (no thinking -> reply cycle)
        assert len(service.messages_api.updated) == 0

    @pytest.mark.asyncio
    async def test_ooo_message_intercepted(self, tmp_path):
        data_dir = str(tmp_path / "data")
        persona = _make_persona(data_dir)

        service = FakeService()
        registry = FakeRegistry(data_dir, persona)

        ooo_items = [
            {
                "eventType": "outOfOffice",
                "summary": "Vacation",
                "end": {"dateTime": "2099-12-31T17:00:00+00:00"},
            }
        ]
        focus_checker = FocusTimeChecker(FakeCalendarService(ooo_items))
        message_buffer = MessageBuffer(str(tmp_path / "buffer"))

        bridge = ChatBridge(
            registry=registry,
            space_id="spaces/test",
            service=service,
            spaces_config=str(tmp_path / "spaces.json"),
            focus_checker=focus_checker,
            message_buffer=message_buffer,
        )

        await bridge.handle_message(_make_message())

        buffered = message_buffer.drain("luna")
        assert len(buffered) == 1

        created_text = service.messages_api.created[0]["body"]["text"]
        assert "Out of Office" in created_text

    @pytest.mark.asyncio
    async def test_multiple_messages_buffered(self, tmp_path):
        data_dir = str(tmp_path / "data")
        persona = _make_persona(data_dir)

        service = FakeService()
        registry = FakeRegistry(data_dir, persona)

        focus_items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T17:00:00+00:00"},
            }
        ]
        focus_checker = FocusTimeChecker(FakeCalendarService(focus_items))
        message_buffer = MessageBuffer(str(tmp_path / "buffer"))

        bridge = ChatBridge(
            registry=registry,
            space_id="spaces/test",
            service=service,
            spaces_config=str(tmp_path / "spaces.json"),
            focus_checker=focus_checker,
            message_buffer=message_buffer,
        )

        await bridge.handle_message(_make_message(text="First message"))
        await bridge.handle_message(_make_message(text="Second message"))

        buffered = message_buffer.drain("luna")
        assert len(buffered) == 2
        assert buffered[0].text == "First message"
        assert buffered[1].text == "Second message"


class TestBridgeNormalFlowWithoutFocus:
    """Bridge integration: normal flow unaffected when not in focus time."""

    @pytest.mark.asyncio
    async def test_normal_flow_when_no_focus_checker(self, tmp_path):
        data_dir = str(tmp_path / "data")
        persona = _make_persona(data_dir)

        service = FakeService()
        registry = FakeRegistry(data_dir, persona)

        bridge = ChatBridge(
            registry=registry,
            space_id="spaces/test",
            service=service,
            spaces_config=str(tmp_path / "spaces.json"),
        )

        await bridge.handle_message(_make_message())

        # Normal flow: thinking message created, then updated with reply
        assert len(service.messages_api.created) == 1
        assert "thinking" in service.messages_api.created[0]["body"]["text"]
        assert len(service.messages_api.updated) == 1
        assert "reply" in service.messages_api.updated[0]["body"]["text"]

    @pytest.mark.asyncio
    async def test_normal_flow_when_focus_checker_returns_none(self, tmp_path):
        data_dir = str(tmp_path / "data")
        persona = _make_persona(data_dir)

        service = FakeService()
        registry = FakeRegistry(data_dir, persona)

        # Focus checker with no events (user not in focus time)
        focus_checker = FocusTimeChecker(FakeCalendarService([]))
        message_buffer = MessageBuffer(str(tmp_path / "buffer"))

        bridge = ChatBridge(
            registry=registry,
            space_id="spaces/test",
            service=service,
            spaces_config=str(tmp_path / "spaces.json"),
            focus_checker=focus_checker,
            message_buffer=message_buffer,
        )

        await bridge.handle_message(_make_message())

        # Normal flow: thinking + reply
        assert len(service.messages_api.created) == 1
        assert "thinking" in service.messages_api.created[0]["body"]["text"]
        assert len(service.messages_api.updated) == 1
        assert "reply" in service.messages_api.updated[0]["body"]["text"]

        # Nothing buffered
        assert message_buffer.drain("luna") == []

    @pytest.mark.asyncio
    async def test_no_interception_when_only_checker_no_buffer(self, tmp_path):
        """Focus checker present but no message_buffer means no interception."""
        data_dir = str(tmp_path / "data")
        persona = _make_persona(data_dir)

        service = FakeService()
        registry = FakeRegistry(data_dir, persona)

        focus_items = [
            {
                "eventType": "focusTime",
                "summary": "Focus Time",
                "end": {"dateTime": "2099-12-31T17:00:00+00:00"},
            }
        ]
        focus_checker = FocusTimeChecker(FakeCalendarService(focus_items))

        bridge = ChatBridge(
            registry=registry,
            space_id="spaces/test",
            service=service,
            spaces_config=str(tmp_path / "spaces.json"),
            focus_checker=focus_checker,
            # message_buffer is None
        )

        await bridge.handle_message(_make_message())

        # Normal flow proceeds because message_buffer is None
        assert len(service.messages_api.created) == 1
        assert "thinking" in service.messages_api.created[0]["body"]["text"]
        assert len(service.messages_api.updated) == 1
