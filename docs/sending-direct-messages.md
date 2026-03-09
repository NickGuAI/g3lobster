# How to DM People

g3lobster supports sending direct messages to users through Google Chat DM spaces and Gmail.

## Google Chat DMs

### Concept

Google Chat DMs work through **DM spaces** — a special type of space between the bot and a single user. To DM someone, you find or create a DM space with that user, then post a message to it.

### Sending a DM Programmatically

Use the Google Chat API through the authenticated service:

```python
from g3lobster.chat.auth import get_authenticated_service

service = get_authenticated_service(auth_data_dir)

# Step 1: Find or create a DM space with the user
try:
    # Try finding existing DM space
    dm_space = service.spaces().findDirectMessage(
        name="users/user@example.com"
    ).execute()
except Exception:
    # Create DM space if none exists
    dm_space = service.spaces().setup(body={
        "space": {
            "spaceType": "DIRECT_MESSAGE",
        },
        "memberships": [
            {"member": {"name": "users/user@example.com", "type": "HUMAN"}}
        ],
    }).execute()

space_name = dm_space["name"]  # e.g., "spaces/dm-XXXXX"

# Step 2: Send message to the DM space
service.spaces().messages().create(
    parent=space_name,
    body={"text": "Hello! This is a direct message from your agent."}
).execute()
```

### Key Points

- The bot must be installed in the user's organization or the user must have previously interacted with the bot
- `findDirectMessage` accepts both email addresses and user resource names (`users/123456`)
- DM spaces are persistent — once created, you can reuse them
- The authenticated user (whose OAuth token is used) must have permission to message the target user

### Using ChatBridge's send_message

The `ChatBridge.send_message()` method currently sends to the configured space. To DM a user, you would need to temporarily switch the target space or call the API directly:

```python
# Direct API call for DM (not through ChatBridge)
await asyncio.to_thread(
    service.spaces().messages().create(
        parent=dm_space_name,
        body={"text": f"{persona.emoji} {persona.name}: {message}"}
    ).execute
)
```

## Email DMs

The `EmailBridge` provides a simpler path for direct messaging:

### Sending to Users

Agents can reply directly to users via email. When an agent receives an email (via plus-addressing), the reply goes directly to the sender's inbox:

```
User sends: helper+luna@example.com
Agent processes and replies via Gmail API
User receives: Re: {original subject} — in their inbox
```

### Configuration

```yaml
email:
  enabled: true
  base_address: "helper@example.com"
  poll_interval_s: 30.0
  auth_data_dir: "./data/email_auth"
```

### How Email Replies Work

The `EmailBridge` sends replies using the Gmail API with proper threading:

```python
# Simplified from email_bridge.py
message = MIMEText(response_text)
message["To"] = sender_email
message["Subject"] = f"Re: {original_subject}"
message["In-Reply-To"] = original_message_id
message["References"] = original_message_id

raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
service.users().messages().send(
    userId="me",
    body={"raw": raw, "threadId": original_thread_id}
).execute()
```

## Alert DMs

The alert system can send direct notifications to admins:

### Via Email
```yaml
alerts:
  enabled: true
  email_address: "admin@example.com"
```

Health alerts (agent_dead, agent_stuck, etc.) are emailed directly to the configured address.

### Via Google Chat
```yaml
alerts:
  enabled: true
  chat_space_id: "spaces/ALERT_SPACE"
```

Alerts are posted to a dedicated Chat space. To make these true DMs, set `chat_space_id` to a DM space between the bot and the admin user.

### Via Webhook
```yaml
alerts:
  enabled: true
  webhook_url: "https://hooks.slack.com/services/..."
```

Alerts can be routed to Slack, Discord, or any webhook endpoint.

## Summary

| Channel | Method | Privacy | Setup |
|---------|--------|---------|-------|
| Google Chat DM | `spaces().findDirectMessage()` + `messages().create()` | Private 1:1 | Bot must be accessible to user |
| Email | Gmail API reply in thread | Private inbox | Plus-addressing configured |
| Alert email | Gmail send to `email_address` | Private inbox | `alerts.email_address` set |
| Alert chat | Post to `chat_space_id` | Space-visible | Use DM space for private alerts |
