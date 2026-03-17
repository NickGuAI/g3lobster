import re

with open("g3lobster/chat/bridge.py", "r") as f:
    content = f.read()

# remove `_tool_name_for_display` and `_format_progress_text`
regex1 = r"def _tool_name_for_display\(event_data: Dict\[str, object\]\) -> str:.*?return \"\"\n\n\n"
content = re.sub(regex1, "", content, flags=re.DOTALL)

regex2 = r"def _format_progress_text\(persona, activity: str\) -> str:.*?return f\"{persona.emoji} _{persona.name} is doing {activity_text}\.\.\._\"\n\n\n"
content = re.sub(regex2, "", content, flags=re.DOTALL)

with open("g3lobster/chat/bridge.py", "w") as f:
    f.write(content)
