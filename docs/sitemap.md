# G3LOBSTER Documentation Sitemap

```
docs/
├── index.md                      ← Start here — project overview, quickstart, architecture
├── sitemap.md                    ← This file
│
├── Core Systems
│   ├── memory-architecture.md    ← Per-agent memory, global memory, ContextBuilder, compaction
│   ├── heartbeat-and-cron.md     ← Heartbeat loop, cron scheduler, health monitor, alerts
│   ├── task-board.md             ← Task board: types, lifecycle, MCP tools, REST API
│   └── gemini-cli-and-persona.md ← SOUL.md, persona config, Gemini CLI process model
│
├── Integrations
│   ├── google-chat-integration.md ← OAuth setup, ChatBridge, polling, commands
│   ├── multi-space-support.md     ← Per-agent spaces, space routing, overrides
│   ├── sending-direct-messages.md ← DM allowlists, DM bridge
│   └── morning-briefing.md        ← Calendar + Gmail daily briefing pipeline
│
├── Operations
│   ├── multi-agent-control.md     ← Registry, pool, delegation, subagents
│   └── cloud-run-deployment.md    ← Dockerfile, Cloud Run config, environment vars
│
└── Architecture
    ├── architecture.mmd           ← Mermaid source diagram
    ├── architecture.svg           ← Rendered SVG
    └── architecture.png           ← Rendered PNG
```

## Reading Order

**New to the project?** Follow this path:

1. `index.md` — what g3lobster is and how to run it
2. `gemini-cli-and-persona.md` — understand the agent model
3. `memory-architecture.md` — understand how agents remember
4. `google-chat-integration.md` — connect to Google Chat
5. `heartbeat-and-cron.md` — schedule recurring work
6. `task-board.md` — manage agent tasks

**Deploying to production?**

1. `cloud-run-deployment.md`
2. `multi-agent-control.md`
3. `multi-space-support.md`
