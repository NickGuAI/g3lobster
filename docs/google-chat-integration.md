# Google Chat and Gmail Integration

g3lobster connects to Google Chat and Gmail as input/output channels for agents. Messages arriving in a Chat space or email inbox are routed to the appropriate agent, and responses are posted back.

## Google Chat (ChatBridge)

### Authentication

g3lobster uses **OAuth 2.0 user-based auth** (not service account):

**Scopes:**
- `chat.messages` — send/read messages
- `chat.spaces` — access spaces
- `chat.memberships.readonly` — list space members
- `chat.users.spacesettings` — user settings

**Token flow:**
1. User uploads `credentials.json` from GCP Console (OAuth client credentials)
2. On first run, opens browser for OAuth consent
3. Token cached at `{auth_data_dir}/token.json` (default `~/.gemini_chat_bridge`)
4. Automatically refreshes expired tokens

**Setup wizard handles this at Step 1 via:**
- `POST /setup/credentials` — upload credentials.json
- `GET /setup/test-auth` — get OAuth consent URL
- `POST /setup/complete-auth` — exchange authorization code for token

### Polling Mechanism

ChatBridge uses **pull-based polling** (not webhooks):

```
Every poll_interval_s (default 2.0s):
  → Fetch up to 20 messages from space (ordered by createTime desc)
  → Filter to messages newer than _last_message_time
  → For each new message:
      skip if sender is not HUMAN
      skip if text is empty
      skip if content_id already in _seen_content (BoundedSet, 10k max)
      → resolve target agent
      → handle message
  → Update _last_message_time
```

On first poll, it captures the latest timestamp and returns — ensuring only new messages are processed going forward.

### Message Routing

When a message arrives, it's routed to an agent using three fallback mechanisms:

**1. Bot User ID mention (primary):**
Messages with `USER_MENTION` annotations are matched against each persona's `bot_user_id`. This is the most reliable routing method.

**2. Name mention (fallback):**
If no bot mention is found, the message text is scanned for `@AgentName` or `@agent-id` patterns.

**3. No match:**
Message is silently ignored.

### Session Isolation

Each conversation gets a unique session ID:

```
session_id = "{space_id}__{user_id}__{thread_id_safe}"
```

- `space_id`: e.g., `spaces/ABCD1234`
- `user_id`: e.g., `users/123`
- `thread_id_safe`: thread path with `/` → `_`, or `"no-thread"`

This ensures conversations are isolated per user per thread.

### Response Flow

```
Message arrives
  → Send "🤖 _AgentName is thinking..._" to thread
  → Assign task to agent runtime
  → On success: "🤖 AgentName: {result}"
  → On failure: "🤖 AgentName: error: {error_text}"
  → On cancel:  "🤖 AgentName: task canceled"
```

### Slash Commands

Before routing to the AI, the bridge intercepts slash commands:

| Command | Syntax | Purpose |
|---------|--------|---------|
| `/help` | `/help` | List available commands |
| `/cron list` | `/cron list` | List agent's cron tasks |
| `/cron add` | `/cron add "0 9 * * *" "do something"` | Create cron task |
| `/cron delete` | `/cron delete <id_prefix>` | Delete cron task |
| `/cron enable` | `/cron enable <id_prefix>` | Enable a task |
| `/cron disable` | `/cron disable <id_prefix>` | Disable a task |

Slash commands are handled locally without hitting the AI.

## Gmail (EmailBridge)

### How It Works

EmailBridge extends g3lobster to accept emails as agent triggers using plus-addressing:

```
helper+luna@example.com  →  routes to agent "luna"
helper+data-bot@example.com  →  routes to agent "data-bot"
```

**Polling:** Every 30 seconds (configurable), queries Gmail for unread messages.

**Processing:**
1. Extract agent ID from `To` header (between `+` and `@`)
2. Combine subject + body as prompt: `[Email: {subject}]\n\n{body}`
3. Session ID: `email__{agent_id}`
4. Reply via Gmail in the same thread with `Re: {subject}`

### Authentication

Same OAuth 2.0 flow as Chat, but with Gmail scope:
- `https://www.googleapis.com/auth/gmail.modify`
- Token cached at `{email_auth_dir}/gmail_token.json`
- Can share `credentials.json` with Chat auth

## Configuration

```yaml
chat:
  enabled: true
  space_id: "spaces/ABCD1234"    # Google Chat space ID
  space_name: "my-workspace"     # Display name for auto-creation
  poll_interval_s: 2.0           # Polling frequency

email:
  enabled: true
  base_address: "helper@example.com"
  poll_interval_s: 30.0
  auth_data_dir: "./data/email_auth"
```

**Environment overrides:**
```bash
G3LOBSTER_CHAT_ENABLED=true
G3LOBSTER_CHAT_SPACE_ID=spaces/ABCD1234
G3LOBSTER_EMAIL_ENABLED=true
G3LOBSTER_EMAIL_BASE_ADDRESS=helper@example.com
```

## Setup Wizard

The web UI at `/ui` walks through setup in 5 steps:

| Step | Action |
|------|--------|
| 1. Credentials | Upload `credentials.json` + complete OAuth flow |
| 2. Space | Enter space ID from Google Chat URL |
| 3. Agent | Create first agent (name, emoji, model, soul) |
| 4. Bot | Detect bots in space, link bot to agent |
| 5. Launch | Start agents + bridge polling |

### Setup API Routes

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/setup/status` | Check setup progress |
| POST | `/setup/credentials` | Upload credentials.json |
| GET | `/setup/test-auth` | Get OAuth consent URL |
| POST | `/setup/complete-auth` | Exchange auth code for token |
| POST | `/setup/space` | Save space configuration |
| GET | `/setup/space-bots` | List BOT members in space |
| POST | `/setup/start` | Start ChatBridge |
| POST | `/setup/stop` | Stop ChatBridge |

## Comparison

| Aspect | ChatBridge | EmailBridge |
|--------|-----------|------------|
| Transport | Google Chat API | Gmail API |
| Routing | Bot mention / @name | Plus-addressing (+agent_id) |
| Session | space + user + thread | email__{agent_id} |
| Replies | Posted to space/thread | Email reply in thread |
| Dedup | Content hash (BoundedSet) | Message ID tracking |
| Visibility | Space-visible | Private (email only) |
| Poll rate | 2s default | 30s default |
