# Video B: Most Transformative Google Workflow (3:00)

## Overview

A three-minute walkthrough showing how g3lobster transforms Google Chat from a passive messaging tool into an autonomous multi-agent workspace. We demonstrate persistent identity via SOUL.md, layered memory (MEMORY.md and PROCEDURES.md), inter-agent delegation, cron autonomy, and the five-step setup wizard -- all running live against a real Google Chat space.

## Pre-Recording Setup

1. **g3lobster server running** at `localhost:40000` with three agents already created and bridge started:
   - `luna` (Creative Lead, emoji: `🌙`, SOUL.md filled with creative-director persona)
   - `data-bot` (Analyst, emoji: `📊`, SOUL.md filled with data-analyst persona)
   - `scheduler` (Ops, emoji: `⏰`, SOUL.md filled with operations persona)
2. **Google Chat space** open in browser (space visible in sidebar, all three bots added to the space).
3. **Terminal** with `data/agents/luna/.memory/MEMORY.md` open in an editor or `cat` ready.
4. **Second terminal** with `data/agents/luna/.memory/PROCEDURES.md` open.
5. **Pre-seed luna's MEMORY.md** with a tagged entry from a "previous session":
   ```
   ## 2026-03-10 User Preferences
   - User prefers bullet-point summaries over paragraph form
   - User timezone is PST
   ```
6. **Pre-seed a cron job** on `scheduler` for the demo:
   ```
   POST localhost:40000/agents/scheduler/crons
   {"schedule": "*/2 * * * *", "instruction": "Check the team task board and post a status summary to chat"}
   ```
7. **Dashboard tab** open at `localhost:40000/ui` on the Launch step (step 5) showing all three agents with green "running" status pills.
8. **A fresh setup instance** ready in a separate browser profile (clean state, no credentials) for the ACT 4 speed-run. Alternatively, pre-record the setup wizard segment.

---

## ACT 1 -- The Problem (0:00-0:30)

**Goal:** Establish pain point -- chatbots today are stateless, identity-less, single-purpose.

### Narration + Screen

[SCREEN: Generic chatbot conversation. Two exchanges visible. In the first, user asks "Summarize in bullets" and bot responds with bullets. In the second -- a new session -- user says "Summarize this report" and bot responds with a wall of paragraphs, having forgotten the preference.]

[TIMING: 0:00-0:10]

> "Every chatbot you have used in Google Chat has the same problem. You tell it something -- it forgets. You configure its tone -- it resets. Each conversation starts from zero."

[SCREEN: Cut to a split view. Left side: the forgetful chatbot. Right side: g3lobster's Google Chat space with three named agents responding with distinct personalities. Luna's message starts with `🌙` and uses creative language. DataBot's starts with `📊` and cites numbers.]

[TIMING: 0:10-0:20]

> "g3lobster fixes this. Every agent has a persistent identity defined in SOUL.md, layered memory that survives across sessions, and the ability to act on its own through scheduled tasks -- all inside Google Chat."

[SCREEN: Quick flash montage -- SOUL.md file, MEMORY.md file, a cron job firing in terminal logs, a delegation call in Google Chat. Each visible for about one second.]

[TIMING: 0:20-0:30]

> "Let me show you how it works."

---

## ACT 2 -- Meet The Team (0:30-1:15)

**Goal:** Introduce named agents with distinct personalities. Show delegation in action.

### Scene 1: Agent Personalities (0:30-0:50)

[SCREEN: Google Chat space. Type a message: "@Luna describe your role on this team"]

[TIMING: 0:30-0:35]

> "We have three agents in this Google Chat space. Each one has a name, an emoji, and a persona defined in their SOUL.md file. Watch how Luna responds."

[SCREEN: Luna's reply appears, prefixed with `🌙`, in her creative-director voice -- something like "I'm your creative lead. I shape ideas into narratives, draft copy, and make sure everything we ship has a spark." The response reflects her SOUL.md persona, not generic bot language.]

[TIMING: 0:35-0:42]

> "That personality comes from SOUL.md -- a plain markdown file stored at `data/agents/luna/SOUL.md` that becomes her system prompt on every request."

[SCREEN: Quickly show the SOUL.md file in terminal or editor. Highlight the persona text that matches her response tone.]

[TIMING: 0:42-0:48]

> "DataBot and Scheduler have their own SOUL files with completely different voices."

[SCREEN: Brief flash of DataBot responding with `📊` prefix in analytical tone, then Scheduler with `⏰` prefix in concise ops tone. These can be pre-captured responses already visible in chat history.]

[TIMING: 0:48-0:50]

### Scene 2: Delegation (0:50-1:15)

[SCREEN: Type in Google Chat: "@Luna I need engagement numbers for last week's campaign. Can you get DataBot to pull them?"]

[TIMING: 0:50-0:55]

> "Now watch delegation. I ask Luna for data -- but she is a creative lead, not an analyst. She knows DataBot is the right agent for the job."

[SCREEN: Luna's response appears showing she is calling `delegate_to_agent`. The dashboard or chat shows Luna's state go from idle to busy, and DataBot's state goes from idle to busy. After a few seconds, Luna replies with DataBot's analysis, attributed and summarized in her own voice.]

[TIMING: 0:55-1:08]

> "Under the hood, Luna called the `delegate_to_agent` MCP tool, which hit the REST endpoint at `/control-plane/delegate`. DataBot spun up, ran the query in its own session, and returned the result. Luna then wrapped it in her creative framing."

[SCREEN: Show the delegation flow briefly in the dashboard -- two agents, parent-child task relationship, both returning to idle state.]

[TIMING: 1:08-1:15]

> "Two agents, one conversation, zero manual wiring."

---

## ACT 3 -- Memory & Autonomy (1:15-2:15)

**Goal:** Three demos proving agents learn, remember, and act independently.

### Demo 1: Memory Recall (1:15-1:35)

[SCREEN: Google Chat. Type: "@Luna summarize this quarterly report" and attach or paste a block of text.]

[TIMING: 1:15-1:20]

> "Last session, I told Luna I prefer bullet-point summaries. That was a different conversation. Let us see if she remembers."

[SCREEN: Luna responds with a bullet-point summary, not paragraphs. Her response may even say something like "Here are your bullets, as you prefer."]

[TIMING: 1:20-1:28]

> "She remembered. Here is why."

[SCREEN: Switch to terminal. Run: `cat data/agents/luna/.memory/MEMORY.md`. Show the file with the tagged entry visible: `## 2026-03-10 User Preferences` containing `User prefers bullet-point summaries over paragraph form`.]

[TIMING: 1:28-1:35]

> "MEMORY.md is her short-term memory. The ContextBuilder reads it on every request and injects it into the prompt alongside her SOUL, matched procedures, and the last twelve messages."

### Demo 2: Procedure Extraction (1:35-1:55)

[SCREEN: Show PROCEDURES.md in editor -- currently sparse or empty except for the header `# PROCEDURES`.]

[TIMING: 1:35-1:38]

> "Now for something deeper. When an agent performs the same workflow repeatedly, g3lobster extracts it into a permanent procedure."

[SCREEN: Show the PROCEDURES.md file with a newly populated entry. The entry follows the real format:]

```
## Deploy App
Trigger: deploy production
Weight: 10.5
Status: permanent

Steps:
1. Check git status
2. Run test suite
3. Push to main
```

[TIMING: 1:38-1:46]

> "This procedure started as a candidate in CANDIDATES.json with a weight of one. Each time the agent saw the same pattern, the weight increased -- with a thirty-day half-life decay to forget stale workflows. Once it crossed ten, it promoted to PROCEDURES.md as permanent knowledge."

[SCREEN: Brief side-by-side. Left: CANDIDATES.json showing a candidate with `weight: 4.2`. Right: PROCEDURES.md showing the promoted procedure with `Weight: 10.5`.]

[TIMING: 1:46-1:55]

> "The agent literally learned a workflow from watching the user. No one configured this."

### Demo 3: Cron Autonomy (1:55-2:15)

[SCREEN: Google Chat space. No one has typed anything. Timestamp visible showing current time.]

[TIMING: 1:55-2:00]

> "The last piece is autonomy. Two minutes ago, I scheduled Scheduler to check the task board every two minutes. Nobody has typed anything. Watch."

[SCREEN: Scheduler's message appears in Google Chat, prefixed with `⏰`, containing a status summary. Something like: "Task board update: 3 items in progress, 1 blocked, 2 completed since last check."]

[TIMING: 2:00-2:08]

> "That fired from a cron job -- a five-field cron expression stored at `data/agents/scheduler/crons.json`. APScheduler triggered it, the agent auto-started, ran the instruction, and posted the result back to chat. No human in the loop."

[SCREEN: Show the cron job JSON briefly in terminal:]

```json
{
  "schedule": "*/2 * * * *",
  "instruction": "Check the team task board and post a status summary to chat",
  "enabled": true
}
```

[TIMING: 2:08-2:12]

> "Run history is kept in a ring buffer -- the last twenty executions per task -- so you can audit what your agents did while you were away."

[SCREEN: Flash the cron history endpoint output: `GET /agents/scheduler/crons/{tid}/history` showing a completed run with `duration_s: 3.2`.]

[TIMING: 2:12-2:15]

---

## ACT 4 -- Setup & Close (2:15-3:00)

**Goal:** Show how fast you can go from zero to running agents. Close with impact.

### Setup Speed-Run (2:15-2:45)

[SCREEN: Fresh browser at `localhost:40000/ui`. The wizard shows step 1: "Credentials" with the stepper bar showing five steps: Credentials, Space, First Agent, Register Bot, Launch.]

[TIMING: 2:15-2:18]

> "Setting this up takes under two minutes. Here is the entire setup wizard."

[SCREEN: Step 1 -- Click "Upload Credentials", select `credentials.json` file. Green "Credentials uploaded" notice. Click "Authenticate", paste OAuth code, click "Complete Auth". Green "OAuth complete" notice. Stepper advances to step 2.]

[TIMING: 2:18-2:24]

> "Step one: upload your Google Cloud OAuth credentials and authorize. That is `POST /setup/credentials` followed by `GET /setup/test-auth` and `POST /setup/complete-auth`."

[SCREEN: Step 2 -- Paste space ID (`spaces/AAQAuxoTw1w`) into the Space ID field, type a space name, click "Save Space". Green "Space saved" notice. Stepper advances to step 3.]

[TIMING: 2:24-2:28]

> "Step two: paste your Google Chat space ID."

[SCREEN: Step 3 -- Type "Luna" in the Name field, `🌙` in Emoji, type a brief persona in the SOUL.md textarea: "You are Luna, a creative lead who speaks with warmth and imagination." Click "Create Agent". Green "Agent created" notice. Stepper advances to step 4.]

[TIMING: 2:28-2:34]

> "Step three: name your first agent, give it an emoji and a persona. This writes `agent.json` and `SOUL.md` to `data/agents/luna/`."

[SCREEN: Step 4 -- Click "Detect Bots in Space", select the bot from the dropdown, click "Link Bot". Green "Bot linked to agent" notice. Stepper advances to step 5.]

[TIMING: 2:34-2:38]

> "Step four: link the Google Chat App to your agent."

[SCREEN: Step 5 -- The Launch panel shows the checklist: credentials uploaded (check), OAuth complete (check), space configured (check), at least one agent (check), bridges running (pending). The bridge table shows Luna with her space ID and "stopped" status. Click "Launch Bridge + Agents". Status pills turn green. The checklist shows all five items checked.]

[TIMING: 2:38-2:44]

> "Step five: launch. The bridge starts polling your space every two seconds. Your agent is live."

[SCREEN: Cut to Google Chat. Type "@Luna hello" and get a response within seconds.]

[TIMING: 2:44-2:45]

### Closing Statement (2:45-3:00)

[SCREEN: Split view. Left: Google Chat space with all three agents' messages visible -- Luna creative, DataBot analytical, Scheduler operational. Right: the g3lobster dashboard showing agents, memory files, cron jobs.]

[TIMING: 2:45-2:52]

> "g3lobster turns Google Chat into something it was never designed to be: a workspace where AI agents have real identity, learn from every interaction, delegate to each other, and act on their own schedule."

[SCREEN: Zoom slowly into the Google Chat space, showing the natural conversation flow between user and agents.]

[TIMING: 2:52-2:57]

> "Persistent memory. Learned procedures. Autonomous cron. Multi-agent delegation. All in Google Chat."

[SCREEN: g3lobster logo or project name centered. URL or GitHub link below.]

[TIMING: 2:57-3:00]

> "This is g3lobster."

---

## Backup Plans

### ACT 1 -- If the contrast demo fails
- **Fallback:** Use pre-recorded screenshots of a generic chatbot forgetting context. The "before" half does not need to be live.
- If the g3lobster side fails to respond, show the SOUL.md and MEMORY.md files directly in the editor and narrate what the response would contain based on those files.

### ACT 2 -- If agent responses are slow or delegation times out
- **Fallback for personality demo:** Pre-capture agent responses in Google Chat before recording. Scroll to them and narrate over the existing messages rather than waiting for live responses.
- **Fallback for delegation:** Show the delegation flow using the REST API directly in terminal: `POST /control-plane/delegate` with `{"parent_agent_id": "luna", "agent_name": "data-bot", "prompt": "...", "wait": true}`. Narrate the JSON response showing `status: completed` with the result text.
- If DataBot fails to start, use `POST /agents/data-bot/restart` to recover, or show the delegation concept via the dashboard's task list.

### ACT 3 -- If memory recall does not work
- **Fallback for memory demo:** Open `data/agents/luna/.memory/MEMORY.md` in the editor and show the entry. Then show the ContextBuilder code at `g3lobster/memory/context.py` line 59 (`build()` method) to prove it reads MEMORY.md into every prompt. Narrate: "Even if the network hiccups, the architecture guarantees this file is read on every single request."
- **Fallback for procedures demo:** Show CANDIDATES.json and PROCEDURES.md side by side in the editor. The files are static markdown and JSON -- they do not depend on a live agent. Walk through the weight progression (candidate at 4.2, promoted at 10.5) using the files themselves.
- **Fallback for cron demo:** If the scheduled task does not fire on time, manually trigger it: `POST /agents/scheduler/crons/{tid}/run`. Show the response JSON with `status: completed` and `result_preview`. Then show the cron history: `GET /agents/scheduler/crons/{tid}/history` to prove execution happened.

### ACT 4 -- If the setup wizard fails mid-flow
- **Fallback:** Pre-record the setup wizard segment at 2x speed. Overlay it as a picture-in-picture or screen capture during narration. The wizard is five HTTP calls (`POST /setup/credentials`, `GET /setup/test-auth`, `POST /setup/complete-auth`, `POST /setup/space`, `POST /setup/start`) -- worst case, show them as curl commands in terminal to prove the flow works without the UI.
- If OAuth fails (common with new GCP projects), skip the live OAuth step and show a pre-authenticated instance. Note: "We completed OAuth before recording to save time."

### General Recovery
- Keep a terminal open with `curl localhost:40000/setup/status` ready. If the UI freezes, hit this endpoint to show the system state as JSON and continue narrating from the API layer.
- If an agent dies mid-demo, the health loop (every 30 seconds) will auto-restart it. Mention this as a feature: "The health loop just detected a dead agent and restarted it automatically -- that is production resilience built in."
