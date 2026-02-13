"""Async Google Chat polling bridge."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Set

from g3lobster.chat.auth import get_authenticated_service
from g3lobster.cli.parser import get_content_id
from g3lobster.tasks.types import Task, TaskStatus

logger = logging.getLogger(__name__)


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
    ):
        self.registry = registry
        self.poll_interval_s = poll_interval_s
        self.service = service
        self.space_name = space_name
        self.spaces_config = Path(spaces_config or (Path.home() / ".gemini" / "chat_bridge_spaces.json"))
        self.auth_data_dir = auth_data_dir

        self.space_id = space_id
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_message_time: Optional[str] = last_message_time
        self._seen_content: Set[str] = seen_content or set()

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
            self._last_message_time = messages[0].get("createTime") if messages else "0"
            return

        for message in reversed(messages):
            create_time = message.get("createTime", "0")
            if create_time <= self._last_message_time:
                continue
            self._last_message_time = create_time
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
        thread_id = message.get("thread", {}).get("name")
        session_id = thread_id or sender.get("name") or "default"

        task = Task(prompt=text, session_id=session_id)

        await self.send_message(
            f"{persona.emoji} _{persona.name} is thinking..._",
            thread_id=thread_id,
        )

        result_task = await runtime.assign(task)
        if result_task.status == TaskStatus.COMPLETED and result_task.result:
            await self.send_message(
                f"{persona.emoji} {persona.name}: {result_task.result}",
                thread_id=thread_id,
            )
        elif result_task.status == TaskStatus.FAILED:
            await self.send_message(
                f"{persona.emoji} {persona.name}: error: {result_task.error}",
                thread_id=thread_id,
            )
        else:
            await self.send_message(
                f"{persona.emoji} {persona.name}: task canceled",
                thread_id=thread_id,
            )

    async def send_message(self, text: str, thread_id: Optional[str] = None) -> None:
        body = {"text": text}
        if thread_id:
            body["thread"] = {"name": thread_id}

        await asyncio.to_thread(
            self.service.spaces().messages().create(parent=self.space_id, body=body).execute
        )
