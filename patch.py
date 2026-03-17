import re

with open("g3lobster/chat/bridge.py", "r") as f:
    content = f.read()

old_code = """        _first_tool_use = False

        async for event in runtime.assign_stream(task):
            stream_events.append(event)

            # Publish streaming event to SSE subscribers
            if self.event_bus:
                self.event_bus.publish(target_id, {
                    "type": event.event_type.value,
                    "data": event.data,
                    "text": event.text or None,
                })

            if event.event_type == StreamEventType.TOOL_USE:
                # 🔥 — generating (first tool use only)
                if not _first_tool_use and user_message_name:
                    _first_tool_use = True
                    reaction_name = await self.transition_reaction(
                        user_message_name, reaction_name, "🔥"
                    )
                tool_name = _tool_name_for_display(event.data)
                progress_text = _format_progress_text(persona, tool_name)
                if thinking_name and progress_text != last_progress_text:
                    await self.update_message(thinking_name, progress_text)
                    last_progress_text = progress_text
                    last_update_time = time.monotonic()
            elif event.event_type == StreamEventType.MESSAGE:
                # 🔥 — generating (first message event, if no tool use triggered it)
                if not _first_tool_use and user_message_name:
                    _first_tool_use = True
                    reaction_name = await self.transition_reaction(
                        user_message_name, reaction_name, "🔥"
                    )
                if event.text:
                    accumulated_text += event.text
                    now = time.monotonic()
                    if thinking_name and now - last_update_time >= self.stream_update_interval_s:
                        progress_text = f"{persona.emoji} {persona.name}: {accumulated_text}"
                        await self.update_message(thinking_name, progress_text)
                        last_progress_text = progress_text
                        last_update_time = now
            elif event.event_type == StreamEventType.RESULT:"""

new_code = """        _first_tool_use = False
        heartbeat_ticks = 0

        async for event in runtime.assign_stream(task):
            stream_events.append(event)

            # Publish streaming event to SSE subscribers
            if self.event_bus:
                self.event_bus.publish(target_id, {
                    "type": event.event_type.value,
                    "data": event.data,
                    "text": event.text or None,
                })

            if event.event_type in (StreamEventType.TOOL_USE, StreamEventType.TOOL_RESULT, StreamEventType.MESSAGE):
                # 🔥 — generating (first tool use/message only)
                if not _first_tool_use and user_message_name:
                    _first_tool_use = True
                    reaction_name = await self.transition_reaction(
                        user_message_name, reaction_name, "🔥"
                    )
                
                if event.event_type == StreamEventType.MESSAGE and event.text:
                    accumulated_text += event.text

                now = time.monotonic()
                if thinking_name and now - last_update_time >= self.stream_update_interval_s:
                    heartbeat_ticks += 1
                    stars = "*" * ((heartbeat_ticks % 5) + 1)
                    progress_text = f"{persona.emoji} ⏳ Thinking{stars}"
                    if progress_text != last_progress_text:
                        await self.update_message(thinking_name, progress_text)
                        last_progress_text = progress_text
                    last_update_time = now
            elif event.event_type == StreamEventType.RESULT:"""

content = content.replace(old_code, new_code)

with open("g3lobster/chat/bridge.py", "w") as f:
    f.write(content)
