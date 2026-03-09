# Multi-Google Chat Space Support

g3lobster supports running agents across multiple Google Chat spaces. The architecture follows a **one service = one space** model, with multiple agents coexisting in each space via bot-based routing.

## Architecture

```
┌─────────────────────────────────────┐
│  g3lobster Service (port 20001)     │
│                                      │
│  ChatBridge ──▶ spaces/ABCD1234     │
│    │                                 │
│    ├── luna  (bot: users/999)       │
│    ├── sonic (bot: users/888)       │
│    └── nova  (bot: users/777)       │
│                                      │
│  All agents share one space,        │
│  routed by @mention                 │
└─────────────────────────────────────┘
```

### Agent-to-Space Binding

- **One ChatBridge** runs per g3lobster service instance
- **One space** is configured per ChatBridge (via `config.chat.space_id`)
- **Multiple agents** can live in the same space, each linked to a different Google Chat bot via `bot_user_id`
- Messages are routed by matching `@BotMention` annotations against persona `bot_user_id` fields

### Linking an Agent to a Bot

Each agent needs a unique Google Chat bot. Link them via:

**REST API:**
```bash
curl -X POST http://localhost:20001/agents/luna/link-bot \
  -H "Content-Type: application/json" \
  -d '{"bot_user_id": "users/999"}'
```

**Setup wizard (Step 4):**
1. Create a Chat App in GCP Console (Chat API > Configuration)
2. Add the bot to the space (Space > `+` > Apps > search)
3. Click "Detect Bots" in wizard — lists all BOT members
4. Select agent + bot to link

**Discover bots in a space:**
```bash
curl http://localhost:20001/setup/space-bots
# {"bots": [{"user_id": "users/999", "display_name": "Luna"}, ...]}
```

## Running Multiple Spaces

### Option A: Multiple g3lobster Services (Recommended)

Run separate instances, each configured with a different space:

```
# Service A — Team Alpha space
G3LOBSTER_CHAT_SPACE_ID=spaces/ALPHA \
G3LOBSTER_SERVER_PORT=20001 \
python -m g3lobster

# Service B — Team Beta space
G3LOBSTER_CHAT_SPACE_ID=spaces/BETA \
G3LOBSTER_SERVER_PORT=20002 \
python -m g3lobster
```

Both services can share the same `data/agents/` directory (shared agent definitions). Each service independently polls its assigned space.

**Separate config files:**
```yaml
# config-alpha.yaml
chat:
  space_id: "spaces/ALPHA"
server:
  port: 20001

# config-beta.yaml
chat:
  space_id: "spaces/BETA"
server:
  port: 20002
```

```bash
python -m g3lobster --config config-alpha.yaml
python -m g3lobster --config config-beta.yaml
```

### Option B: Multiple Agents in One Space

If all agents should be in the same space, use a single service with multiple agents:

1. Create agents via API or wizard
2. Create separate Chat bots in GCP Console (one per agent)
3. Add all bots to the space
4. Link each agent to its bot via `/agents/{id}/link-bot`

Users mention the relevant bot to route messages: `@Luna help me research` or `@Sonic run the tests`.

## Space Configuration

### Auto-Discovery

When no `space_id` is configured, ChatBridge auto-creates a space:

1. Check `~/.gemini/chat_bridge_spaces.json` for a mapping from current working directory to space
2. If found, reuse that space
3. If not, create new space via `service.spaces().setup()` with display name from config
4. Store the mapping for future runs

**spaces_config format:**
```json
{
  "/path/to/workspace-a": "spaces/ABCD1234",
  "/path/to/workspace-b": "spaces/EFGH5678"
}
```

### Manual Configuration

Set `space_id` directly in config:

```yaml
chat:
  space_id: "spaces/ABCD1234"
  space_name: "My Team Space"
```

Or via environment: `G3LOBSTER_CHAT_SPACE_ID=spaces/ABCD1234`

Or via setup wizard: `POST /setup/space` with `{"space_id": "spaces/ABCD1234"}`.

The space ID is normalized automatically — `space/XYZ`, `spaces/XYZ`, and bare `XYZ` are all accepted.

## Replicating an Agent to a New Space

To use the same agent persona in multiple spaces:

1. **Deploy a new g3lobster service** pointed at the new space
2. The new service reads the same `data/agents/` directory
3. Create a new Chat bot in GCP Console for the new space
4. Add the bot to the new space
5. Link the bot: `POST /agents/{id}/link-bot` with the new bot's user ID

**Or clone the agent:**
```bash
# Copy agent data
cp -r data/agents/luna data/agents/luna-beta

# Update the clone's identity
curl -X PUT http://localhost:20002/agents/luna-beta \
  -H "Content-Type: application/json" \
  -d '{"name": "Luna (Beta)", "bot_user_id": "users/new-bot-id"}'
```

## Message Routing Details

When a message arrives in the space:

```
Message with @BotMention
  │
  ├─ Extract USER_MENTION annotations
  │   └─ Match bot user.name against persona.bot_user_id
  │      → Found: route to that agent
  │
  ├─ Fallback: scan text for @AgentName or @agent-id
  │   → Found: route to that agent
  │
  └─ No match: message ignored
```

Multiple agents in the same space are independent — they don't see each other's conversations unless explicitly delegating via the delegation MCP.

## Session Isolation

Even in a shared space, conversations are isolated:

```
session_id = "{space_id}__{user_id}__{thread_id_safe}"
```

- Different users talking to the same agent get separate sessions
- Different threads from the same user get separate sessions
- Each session has its own JSONL transcript and memory context
