# Morning Briefing Agent

A cron-scheduled agent that pulls Calendar events, Gmail priority threads, and Chat mentions, then delivers a synthesized morning briefing as a Google Chat DM.

## Quick Start

### 1. Enable Calendar OAuth Scope

The morning briefing requires the `calendar.events.readonly` scope. If you set up OAuth before this feature was added, you'll need to re-authorize:

```bash
# Delete the existing token to force re-auth
rm ~/.gemini_chat_bridge/token.json

# Restart g3lobster — it will prompt for OAuth consent with the new scope
```

### 2. Create the Agent

The `morning-briefing` agent template is included at `data/agents/morning-briefing/`. If you need to create it manually:

```bash
curl -X POST http://localhost:20001/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "id": "morning-briefing",
    "name": "Morning Briefer",
    "emoji": "\ud83c\udf05"
  }'
```

### 3. Configure the Cron Task

```bash
# Via Chat slash command (from any space with the bot):
/cron add "0 7 * * 1-5" "Generate and deliver my morning briefing"

# Via REST API:
curl -X POST http://localhost:20001/api/agents/morning-briefing/crons \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": "0 7 * * 1-5",
    "instruction": "Generate and deliver my morning briefing",
    "dm_target": "user@example.com"
  }'
```

### 4. Enable Calendar Integration

Add to your `config.yaml`:

```yaml
calendar:
  enabled: true
  # auth_data_dir: ""  # defaults to chat auth dir
```

## Configuration

### config.yaml

```yaml
calendar:
  enabled: true           # Toggle Calendar API integration
  auth_data_dir: ""       # OAuth dir (defaults to chat auth dir if empty)

cron:
  enabled: true           # Must be true for scheduled briefings
```

### Cron Task Fields

| Field | Description | Example |
|-------|-------------|---------|
| `schedule` | 5-field cron expression | `0 7 * * 1-5` (7 AM weekdays) |
| `instruction` | Prompt for the agent | `"Generate morning briefing"` |
| `dm_target` | Email for DM delivery | `"user@example.com"` |
| `enabled` | Toggle task on/off | `true` |

### DM Target

The `dm_target` field on a cron task specifies who receives the result via Google Chat DM. When set, the cron manager automatically sends the agent's output to that user after execution.

This field is available on any cron task, not just morning briefings.

## Architecture

```
Cron Trigger (APScheduler)
    |
    v
CronManager._fire()
    |
    v
morning-briefing agent (AgentPersona)
    |
    v
briefing/run.py -> gather.py -> formatter.py
    |
    v
chat/dm.py -> Google Chat DM
```

### Module Layout

| Module | Purpose |
|--------|---------|
| `g3lobster/briefing/gather.py` | Data fetching: Calendar, Gmail, Chat |
| `g3lobster/briefing/formatter.py` | Renders data into plaintext digest |
| `g3lobster/briefing/run.py` | Orchestrates gather → format → send |
| `g3lobster/chat/dm.py` | Google Chat DM helper (reusable) |

## OAuth Scopes

The briefing agent uses the same OAuth token as the Chat bridge, with an additional scope:

| Scope | Purpose |
|-------|---------|
| `chat.messages` | Send/read Chat messages |
| `chat.spaces` | Access Chat spaces |
| `chat.memberships.readonly` | Read space memberships |
| `chat.users.spacesettings` | User space settings |
| `calendar.events.readonly` | Read Calendar events |

Gmail uses a separate OAuth flow via `EmailBridge` with the `gmail.modify` scope.

## Customization

### Briefing Content

The agent's SOUL.md (at `data/agents/morning-briefing/SOUL.md`) controls the briefing style and format. Edit it to customize:

- Which sections appear
- How events are summarized
- Tone and formatting preferences

### Schedule

Change the cron schedule via the API or Chat commands:

```bash
# Update to 8:30 AM daily
curl -X PUT http://localhost:20001/api/agents/morning-briefing/crons/{task_id} \
  -H "Content-Type: application/json" \
  -d '{"schedule": "30 8 * * *"}'
```

## Troubleshooting

### "OAuth token missing" Error

Re-run the auth setup. The Calendar scope was added and requires re-consent:

```bash
rm ~/.gemini_chat_bridge/token.json
# Restart g3lobster and complete OAuth
```

### No Calendar Events Showing

1. Verify `calendar.enabled: true` in config.yaml
2. Check that the OAuth token has the `calendar.events.readonly` scope
3. Ensure the user has events on their primary calendar

### DM Not Delivered

1. Verify `dm_target` is set on the cron task
2. The bot must be accessible to the target user (same org or prior interaction)
3. Check logs for `Failed to deliver briefing DM` errors
