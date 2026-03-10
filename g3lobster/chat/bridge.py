"""Async Google Chat polling bridge."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Set

if TYPE_CHECKING:
    from g3lobster.cron.store import CronStore

from g3lobster.chat.auth import get_authenticated_service
from g3lobster.chat.commands import handle as handle_command
from g3lobster.cli.parser import get_content_id
from g3lobster.cli.streaming import StreamEventType, accumulate_text
from g3lobster.tasks.types import Task, TaskStatus
from g3lobster.utils import BoundedSet

logger = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string returned by the Google Chat API.

    Handles both ``Z`` suffix and ``+00:00`` offset forms, and varying
    fractional-second precision.  Falls back to the Unix epoch on parse
    failure so comparisons remain safe.
    """
    try:
        # Python < 3.11 does not accept the trailing 'Z' in fromisoformat.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return _EPOCH


def _tool_name_for_display(event_data: Dict[str, object]) -> str:
    """Extract a displayable tool name across Gemini CLI schema variants."""
    for key in ("tool_name", "toolName", "tool", "name"):
        value = event_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _format_progress_text(persona, activity: str) -> str:
    activity_text = activity.strip()
    if not activity_text:
        return f"{persona.emoji} _{persona.name} is thinking..._"
    return f"{persona.emoji} _{persona.name} is doing {activity_text}..._"


def _resolve_task_timeout_s(persona: object, registry: object) -> Optional[float]:
    timeout_s = getattr(persona, "response_timeout_s", None)
    if timeout_s is None:
        timeout_s = getattr(registry, "gemini_timeout_s", 120.0)
    if timeout_s is None:
        return None
    return float(timeout_s)


class ChatBridge:
    """Polls Google Chat and forwards messages to named agents."""

    def __init__(
        self,
        registry,
        space_id: Optional[str],
        poll_interval_s: float = 2.0,
        service=None,
        spaces_config: Optional[str] = None,
        space_name: Optional[str] = None,
        last_message_time: Optional[str] = None,
        seen_content: Optional[Set[str]] = None,
        auth_data_dir: Optional[str] = None,
        cron_store: Optional["CronStore"] = None,
        seen_content_max_size: int = 10_000,
        debug_mode: bool = False,
    ):
        self.registry = registry
        self.poll_interval_s = poll_interval_s
        self.service = service
        self.space_name = space_name
        self.spaces_config = Path(spaces_config or (Path.home() / ".gemini" / "chat_bridge_spaces.json"))
        self.auth_data_dir = auth_data_dir
        self.cron_store = cron_store
        self.debug_mode = debug_mode

        self.space_id = space_id
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_message_time: Optional[str] = last_message_time
        self._seen_content: BoundedSet = BoundedSet(seen_content_max_size)
        if seen_content:
            for item in seen_content:
                self._seen_content.add(item)

    async def start(self) -> None:
        if self.service is None:
            self.service = await asyncio.to_thread(get_authenticated_service, self.auth_data_dir)
        if not self.space_id:
            self.space_id = await self._ensure_space()

        if self._poll_task and not self._poll_task.done():
            return

        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self.poll_loop(), name="g3lobster-chat-poll")

    @property
    def is_running(self) -> bool:
        return bool(self._poll_task and not self._poll_task.done())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _ensure_space(self) -> str:
        known_spaces: Dict[str, str] = {}
        if self.spaces_config.exists():
            try:
                known_spaces = json.loads(self.spaces_config.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                known_spaces = {}

        cwd = str(Path.cwd())
        if cwd in known_spaces:
            return known_spaces[cwd]

        display_name = self.space_name or f"Gemini: {Path(cwd).name}"
        body = {"space": {"display_name": f"🤖 {display_name}", "space_type": "SPACE"}}
        result = await asyncio.to_thread(self.service.spaces().setup(body=body).execute)
        space_id = result["name"]

        known_spaces[cwd] = space_id
        self.spaces_config.parent.mkdir(parents=True, exist_ok=True)
        self.spaces_config.write_text(json.dumps(known_spaces, indent=2), encoding="utf-8")
        return space_id

    async def poll_loop(self) -> None:
        logger.info("Chat bridge polling started for %s", self.space_id)
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Chat bridge poll error")
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(self.poll_interval_s)

    async def _poll_once(self) -> None:
        response = await asyncio.to_thread(
            self.service.spaces()
            .messages()
            .list(parent=self.space_id, pageSize=20, orderBy="createTime desc")
            .execute
        )
        messages = response.get("messages", [])

        if self._last_message_time is None:
            self._last_message_time = messages[0].get("createTime") if messages else ""
            return

        last_dt = _parse_ts(self._last_message_time) if self._last_message_time else _EPOCH
        for message in reversed(messages):
            create_time = message.get("createTime", "")
            if _parse_ts(create_time) <= last_dt:
                continue
            self._last_message_time = create_time
            last_dt = _parse_ts(create_time)
            await self.handle_message(message)

    def _resolve_target_agent(self, message: dict, text: str) -> Optional[str]:
        personas = self.registry.list_enabled_personas()
        if not personas:
            return None

        for annotation in message.get("annotations", []):
            if annotation.get("type") != "USER_MENTION":
                continue
            user = annotation.get("userMention", {}).get("user", {})
            if user.get("type") != "BOT":
                continue

            bot_name = str(user.get("name", "")).strip()
            bot_display = str(user.get("displayName", "")).strip()
            logger.info("Bot mentioned — user_id: %s  display: %s", bot_name, bot_display)

            candidates = {bot_name, bot_display}
            candidates = {item for item in candidates if item}
            for persona in personas:
                if persona.bot_user_id and persona.bot_user_id in candidates:
                    return persona.id

            logger.warning(
                "Bot %s is not linked to any agent. "
                "Go to the wizard step 4 and paste this as the Bot User ID: %s",
                bot_display or bot_name,
                bot_name,
            )

        lowered = text.lower()
        for persona in personas:
            if f"@{persona.name}".lower() in lowered:
                return persona.id
            if f"@{persona.id}".lower() in lowered:
                return persona.id

        return None

    async def handle_message(self, message: dict) -> None:
        sender = message.get("sender", {})
        if sender.get("type") != "HUMAN":
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        content_id = get_content_id(text)
        if content_id in self._seen_content:
            return
        self._seen_content.add(content_id)

        target_id = self._resolve_target_agent(message, text)
        if not target_id:
            return

        runtime = self.registry.get_agent(target_id)
        if not runtime:
            started = await self.registry.start_agent(target_id)
            if not started:
                return
            runtime = self.registry.get_agent(target_id)
            if not runtime:
                return

        persona = runtime.persona

        # Enforce per-agent DM allowlist for direct message spaces
        space_type = message.get("space", {}).get("spaceType", "")
        if space_type == "DIRECT_MESSAGE":
            allowlist = list(getattr(persona, "dm_allowlist", []) or [])
            if allowlist:
                sender_name = sender.get("name", "")
                sender_email = sender.get("email", "")
                if sender_name not in allowlist and sender_email not in allowlist:
                    return

        thread_id = message.get("thread", {}).get("name")
        user_id = sender.get("name") or "unknown"
        thread_id_safe = (thread_id or "no-thread").replace("/", "_")
        session_id = f"{self.space_id}__{user_id}__{thread_id_safe}"

        # Slash-command interception — handle locally without hitting the AI.
        if self.cron_store is not None:
            cmd_reply = handle_command(text, target_id, self.cron_store)
            if cmd_reply is not None:
                await self.send_message(
                    f"{persona.emoji} {persona.name}: {cmd_reply}",
                    thread_id=thread_id,
                )
                return

        task = Task(
            prompt=text,
            session_id=session_id,
            timeout_s=_resolve_task_timeout_s(persona, self.registry),
        )

        thinking_msg = await self.send_message(
            f"{persona.emoji} _{persona.name} is thinking..._",
            thread_id=thread_id,
        )
        thinking_name: Optional[str] = thinking_msg.get("name") if thinking_msg else None
        last_progress_text = f"{persona.emoji} _{persona.name} is thinking..._"
        stream_events = []

        final_result: Optional[str] = None
        final_error: Optional[str] = None

        async for event in runtime.assign_stream(task):
            stream_events.append(event)
            if event.event_type == StreamEventType.TOOL_USE:
                tool_name = _tool_name_for_display(event.data)
                progress_text = _format_progress_text(persona, tool_name)
                if thinking_name and progress_text != last_progress_text:
                    await self.update_message(thinking_name, progress_text)
                    last_progress_text = progress_text
            elif event.event_type == StreamEventType.RESULT:
                if event.data.get("status") == "error":
                    error_data = event.data.get("error") or {}
                    if isinstance(error_data, dict):
                        final_error = str(error_data.get("message") or "unknown error")
                elif event.text:
                    final_result = event.text
            elif event.event_type == StreamEventType.ERROR:
                if event.data.get("severity") == "error":
                    final_error = str(event.data.get("message") or event.data.get("error") or "unknown error")

        if not final_error and task.status == TaskStatus.FAILED:
            final_error = task.error or "unknown error"
        if not final_result:
            final_result = (task.result or accumulate_text(stream_events)).strip()

        if task.status == TaskStatus.FAILED and final_error:
            reply_text = f"{persona.emoji} {persona.name}: error: {final_error}"
            if self.debug_mode:
                reply_text += f"\n```\n{final_error}\n```"
        elif final_result:
            reply_text = f"{persona.emoji} {persona.name}: {final_result}"
        elif final_error:
            reply_text = f"{persona.emoji} {persona.name}: error: {final_error}"
            if self.debug_mode:
                reply_text += f"\n```\n{final_error}\n```"
        else:
            reply_text = f"{persona.emoji} {persona.name}: task finished with no output"

        # Update the thinking message in-place (no extra new message).
        if thinking_name:
            await self.update_message(thinking_name, reply_text)
        else:
            await self.send_message(reply_text, thread_id=thread_id)

    async def send_message(self, text: str, thread_id: Optional[str] = None) -> dict:
        body = {"text": text}
        if thread_id:
            body["thread"] = {"name": thread_id}

        result = await asyncio.to_thread(
            self.service.spaces().messages().create(parent=self.space_id, body=body).execute
        )
        return result or {}

    async def update_message(self, message_name: str, text: str) -> None:
        body = {"text": text}
        try:
            await asyncio.to_thread(
                self.service.spaces()
                .messages()
                .update(name=message_name, updateMask="text", body=body)
                .execute
            )
        except Exception:
            logger.debug("Failed to update message %s", message_name, exc_info=True)
