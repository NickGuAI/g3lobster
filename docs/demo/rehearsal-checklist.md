# Rehearsal Checklist

Two 3-minute hackathon demo videos for g3lobster.
Use this checklist verbatim for every rehearsal and the final recording session.

---

## T-24 Hours: Environment Prep

### Server & Data

- [ ] `make run` starts cleanly on `localhost:40000` with no errors in terminal
- [ ] `localhost:40000/ui` loads the setup wizard / agent dashboard
- [ ] Demo seed script executed (or agents manually created via API/UI)
- [ ] `GET /agents` returns exactly 3 agents:
  - Luna (emoji: `moon`, role: creative lead)
  - DataBot (emoji: `chart_with_upwards_trend`, role: analyst)
  - Scheduler (emoji: `alarm_clock`, role: ops)
- [ ] Each agent responds to `POST /agents/{id}/test` with a coherent reply
- [ ] `config.yaml` reviewed -- no stale values, ports match, `chat.enabled` set as needed

### Recording Setup

- [ ] Screen recording software installed and tested (OBS / macOS Screencast / Loom)
- [ ] Recording resolution set to **1920 x 1080** (or retina equivalent)
- [ ] Frame rate set to 30 fps minimum
- [ ] Microphone tested -- record a 10-second clip, play it back, confirm clarity
- [ ] Audio input level peaks around -12 dB (not clipping, not too quiet)
- [ ] Pop filter or mic distance adjusted to avoid plosives

---

## T-4 Hours: Pre-Flight Checks

### Agent Verification

- [ ] `GET /agents` returns 3 agents with correct `name`, `emoji`, and `soul` fields
- [ ] Luna's `agent.json` has `soul` referencing creative-lead persona
- [ ] DataBot's `agent.json` has `soul` referencing analyst persona
- [ ] Scheduler's `agent.json` has `soul` referencing ops persona
- [ ] Each agent's `SOUL.md` contains a distinct, demo-worthy personality description

### Memory Pre-Seeding

- [ ] Luna's `.memory/MEMORY.md` contains at least 3 entries (e.g., project context, user preferences, past decisions)
- [ ] DataBot's `.memory/MEMORY.md` contains at least 2 entries (e.g., data sources, report history)
- [ ] Scheduler's `.memory/MEMORY.md` contains at least 2 entries (e.g., recurring meeting notes, timezone prefs)
- [ ] Verify via `GET /agents/{id}/memory` -- each returns non-empty `content`

### Procedural Memory

- [ ] Luna's `.memory/PROCEDURES.md` pre-populated with at least 1 procedure (e.g., "How to run a brainstorm")
- [ ] DataBot's `.memory/PROCEDURES.md` pre-populated (e.g., "How to generate a weekly report")
- [ ] Verify via `GET /agents/{id}/procedures` -- returns non-empty `content`

### Cron Tasks

- [ ] At least 1 cron task configured (e.g., Scheduler's daily standup reminder)
- [ ] `GET /agents/{scheduler_id}/crons` returns the expected task(s)
- [ ] Manually trigger with `POST /agents/{scheduler_id}/crons/{task_id}/run` -- confirm it executes
- [ ] `GET /agents/{scheduler_id}/crons/{task_id}/history` shows the test run

### Delegation

- [ ] Send Luna a prompt that triggers delegation to DataBot (e.g., "Get me the latest metrics")
- [ ] Confirm DataBot receives the delegated task via `GET /agents/{databot_id}/tasks`
- [ ] Confirm the task completes and Luna gets the result back

### Google Chat Integration

- [ ] `credentials.json` present and valid in project root
- [ ] OAuth token refreshed (not expired)
- [ ] Chat bridge starts without errors (`POST /bridge/start` or via UI toggle)
- [ ] Google Chat space created and all 3 agents linked (`POST /agents/{id}/link-bot`)
- [ ] Send a test @mention in the Chat space -- correct agent responds
- [ ] Bridge reconnects after a brief network interruption (toggle Wi-Fi, confirm recovery)

### Setup Wizard

- [ ] Complete a full wizard run from scratch in an incognito browser window
- [ ] Wizard detects existing config gracefully (shows "already configured" state)

---

## T-1 Hour: Final Prep

### Desktop Environment

- [ ] Close all apps except: terminal, browser, screen recorder
- [ ] Quit Slack, Discord, email, calendar -- anything that produces notifications
- [ ] Enable **Do Not Disturb** at the OS level
- [ ] Hide desktop icons (macOS: `defaults write com.apple.finder CreateDesktop false && killall Finder`)
- [ ] Hide browser bookmarks bar if it contains personal bookmarks

### Terminal & Browser

- [ ] Terminal font size increased to **16pt+** (readable at 1080p)
- [ ] Terminal theme: dark background, high-contrast text
- [ ] Terminal prompt simplified (remove clutter -- short PS1)
- [ ] Browser zoom set to **125%** for UI pages
- [ ] All demo URLs bookmarked in a "Demo" folder:
  - `http://localhost:40000/ui` (dashboard)
  - `http://localhost:40000/docs` (Swagger, if needed)
  - Google Chat space URL
- [ ] Browser has exactly one clean window with the bookmarked tabs pre-loaded
- [ ] Clear terminal scrollback (`clear && printf '\e[3J'`)

### Final Server State

- [ ] Restart server fresh: `make run`
- [ ] Verify all 3 agents: `curl -s localhost:40000/agents | python3 -m json.tool`
- [ ] Verify crons: `curl -s localhost:40000/agents/{scheduler_id}/crons | python3 -m json.tool`
- [ ] Open the UI dashboard and confirm all agents show "running" status

---

## Video A Recording Checklist

> **Theme:** Platform walkthrough -- create, configure, and interact with agents via UI and API.

### ACT 1: Introduction & Setup (0:00 -- 0:45)

- [ ] **Open on:** UI dashboard at `localhost:40000/ui`
- [ ] **Say:** "g3lobster is a Google Chat agent platform. Let me show you what it can do."
- [ ] **Action:** Show the 3 pre-created agents in the dashboard (Luna, DataBot, Scheduler)
- [ ] **Action:** Click into Luna's detail view -- show SOUL.md, memory, status
- [ ] **Timing check:** This section must finish by **0:45**
- [ ] **Expected output:** Agent detail page loads in < 1 second, all fields populated

### ACT 2: Memory & Procedures (0:45 -- 1:45)

- [ ] **Action:** Show Luna's memory page -- highlight pre-seeded entries
- [ ] **Action:** Open terminal, run:
  ```
  curl -s localhost:40000/agents/luna/memory | python3 -m json.tool
  ```
- [ ] **Expected output:** JSON with `content` field containing memory entries
- [ ] **Action:** Show Luna's procedures:
  ```
  curl -s localhost:40000/agents/luna/procedures | python3 -m json.tool
  ```
- [ ] **Expected output:** JSON with procedure content
- [ ] **Say:** "Every agent builds persistent memory and learns procedures from interactions."
- [ ] **Timing check:** This section must finish by **1:45**

### ACT 3: Cron & Scheduling (1:45 -- 2:30)

- [ ] **Action:** Show Scheduler's cron tasks in the UI
- [ ] **Action:** Trigger a cron run via API:
  ```
  curl -s -X POST localhost:40000/agents/{scheduler_id}/crons/{task_id}/run
  ```
- [ ] **Expected output:** 200 OK with task execution confirmation
- [ ] **Action:** Show cron history:
  ```
  curl -s localhost:40000/agents/{scheduler_id}/crons/{task_id}/history | python3 -m json.tool
  ```
- [ ] **Expected output:** History array with at least 1 completed entry
- [ ] **Say:** "Agents can run tasks on a schedule -- standup reminders, reports, whatever you need."
- [ ] **Timing check:** This section must finish by **2:30**

### ACT 4: Delegation & Wrap-Up (2:30 -- 3:00)

- [ ] **Action:** Send Luna a task that delegates to DataBot (via UI or API `POST /agents/luna/test`)
- [ ] **Expected output:** Luna's response references DataBot's contribution
- [ ] **Say:** "Agents collaborate. Luna just delegated analysis work to DataBot and got results back."
- [ ] **Action:** Return to dashboard -- show all 3 agents active
- [ ] **Say:** "That's g3lobster -- persistent agents, layered memory, scheduled tasks, and multi-agent delegation."
- [ ] **Timing check:** Must finish by **3:00**

---

## Video B Recording Checklist

> **Theme:** Google Chat integration -- real-world usage through Chat spaces.

### ACT 1: Bridge Setup & Chat Space (0:00 -- 0:50)

- [ ] **Open on:** Terminal showing server running
- [ ] **Say:** "g3lobster agents live inside Google Chat. Let me show you the full loop."
- [ ] **Action:** Show `config.yaml` with chat settings highlighted
- [ ] **Action:** Start the chat bridge (via UI toggle or API)
- [ ] **Expected output:** Terminal logs show "Bridge connected" or equivalent
- [ ] **Action:** Switch to Google Chat -- show the demo space with agents present
- [ ] **Timing check:** This section must finish by **0:50**

### ACT 2: Agent Interaction in Chat (0:50 -- 1:50)

- [ ] **Action:** In Google Chat, type: `@Luna What's the status of our creative brief?`
- [ ] **Expected output:** Luna responds in-chat with a personality-consistent answer referencing her memory
- [ ] **Action:** Type: `@DataBot Summarize last week's metrics`
- [ ] **Expected output:** DataBot responds with a data-flavored summary
- [ ] **Say:** "Each agent has its own identity, memory, and expertise. They respond to @mentions."
- [ ] **Timing check:** This section must finish by **1:50**

### ACT 3: Cross-Agent Collaboration in Chat (1:50 -- 2:30)

- [ ] **Action:** Type: `@Luna I need a campaign plan with data backing`
- [ ] **Expected output:** Luna responds, referencing delegation to DataBot for the data component
- [ ] **Action:** Show the delegation happening in the UI dashboard (task list or logs)
- [ ] **Say:** "Luna automatically pulled in DataBot for the data work. Agents coordinate behind the scenes."
- [ ] **Timing check:** This section must finish by **2:30**

### ACT 4: Scheduled Tasks & Close (2:30 -- 3:00)

- [ ] **Action:** Show a cron-triggered message arriving in the Chat space from Scheduler
  (trigger manually with `POST /agents/{scheduler_id}/crons/{task_id}/run` if needed)
- [ ] **Expected output:** Scheduler posts a standup reminder or daily digest in the Chat space
- [ ] **Say:** "Scheduler runs on a cron -- daily standups, weekly reports, fully automated."
- [ ] **Action:** Return to UI dashboard -- show all 3 agents active, bridge connected
- [ ] **Say:** "g3lobster: persistent agents in Google Chat with memory, collaboration, and automation."
- [ ] **Timing check:** Must finish by **3:00**

---

## Backup Plans

### Pre-Recorded Clips

Keep a folder `demo-backups/` with these clips ready to splice in during editing:

| Segment | Backup Clip Filename | What It Shows |
|---|---|---|
| Video A, ACT 1 | `a1-dashboard-tour.mp4` | UI dashboard with 3 agents |
| Video A, ACT 2 | `a2-memory-api.mp4` | curl commands returning memory JSON |
| Video A, ACT 3 | `a3-cron-trigger.mp4` | Cron execution and history |
| Video A, ACT 4 | `a4-delegation.mp4` | Luna-to-DataBot delegation |
| Video B, ACT 1 | `b1-bridge-connect.mp4` | Chat bridge startup |
| Video B, ACT 2 | `b2-chat-mentions.mp4` | @mention responses in Google Chat |
| Video B, ACT 3 | `b3-chat-delegation.mp4` | Cross-agent delegation in Chat |
| Video B, ACT 4 | `b4-cron-chat.mp4` | Scheduled message in Chat space |

### Common Failure Recovery

| Failure | Symptom | Recovery |
|---|---|---|
| Server crash | Terminal shows traceback | `make run` -- takes ~3 seconds. Resume from current ACT. |
| Agent not responding | `POST /agents/{id}/test` returns 500 or hangs | `POST /agents/{id}/restart`, wait 2 seconds, retry. |
| API timeout | curl hangs for > 5 seconds | Ctrl+C, retry once. If still failing, use backup clip. |
| Chat bridge disconnect | Messages in Google Chat get no response | `POST /bridge/stop` then `POST /bridge/start`. Check `credentials.json` expiry. |
| OAuth token expired | Bridge logs show 401 / "invalid_grant" | Re-run OAuth flow: delete `token.json`, restart bridge, re-authorize. **Use backup clip for this segment.** |
| Agent returns garbled output | Response is empty or malformed | Check `SOUL.md` is not empty. Restart the agent. Retry. |
| UI not loading | Browser shows connection refused | Confirm server is running. Check port 40000 is not in use by another process. |
| Cron task fails | `POST .../run` returns error | Check cron config in agent data dir. Re-create the cron via `POST /agents/{id}/crons`. |

---

## Post-Recording

### Timing & Content

- [ ] Video A total duration is **3:00 or under**
- [ ] Video B total duration is **3:00 or under**
- [ ] No dead air longer than 2 seconds
- [ ] No visible hesitation or repeated actions (trim in edit)

### Audio

- [ ] Audio levels consistent across all segments (-12 dB to -6 dB peaks)
- [ ] No background noise, keyboard clatter, or room echo
- [ ] Voiceover is synchronized with on-screen actions (no more than 0.5s drift)

### Privacy & Security

- [ ] No real email addresses visible in Google Chat
- [ ] No API keys, tokens, or credentials shown on screen
- [ ] No personal browser history, bookmarks, or tabs visible
- [ ] `credentials.json` and `token.json` never shown in terminal output
- [ ] Agent memory does not contain real user data

### Export

- [ ] Export format: MP4, H.264, AAC audio
- [ ] Resolution: 1920 x 1080
- [ ] Frame rate: 30 fps
- [ ] Bitrate: 8 Mbps minimum for crisp text
- [ ] File naming: `g3lobster-demo-A-v{N}.mp4`, `g3lobster-demo-B-v{N}.mp4`
- [ ] Play exported file start-to-finish on a different device to confirm quality

---

## Voiceover Script Timing Guide

### Video A

| ACT | Start | End | Duration | Key Talking Points |
|---|---|---|---|---|
| 1: Intro & Setup | 0:00 | 0:45 | 45s | What g3lobster is. Show dashboard. Introduce 3 agents by name and role. |
| 2: Memory & Procedures | 0:45 | 1:45 | 60s | Persistent memory survives restarts. Procedures are auto-extracted. Show API + UI. |
| 3: Cron & Scheduling | 1:45 | 2:30 | 45s | Agents run tasks on schedules. Trigger a cron. Show execution history. |
| 4: Delegation & Wrap-Up | 2:30 | 3:00 | 30s | Multi-agent collaboration. Luna delegates to DataBot. Closing statement. |

### Video B

| ACT | Start | End | Duration | Key Talking Points |
|---|---|---|---|---|
| 1: Bridge Setup | 0:00 | 0:50 | 50s | Google Chat integration. Show config. Start bridge. Show Chat space. |
| 2: Chat Interaction | 0:50 | 1:50 | 60s | @mention Luna and DataBot. Each has distinct personality. Memory-informed responses. |
| 3: Collaboration | 1:50 | 2:30 | 40s | Delegation happens transparently in Chat. Show it in UI. |
| 4: Scheduled & Close | 2:30 | 3:00 | 30s | Cron-triggered Chat messages. Closing statement with all agents running. |

### Pacing Notes

- Speak at ~150 words per minute (natural, not rushed)
- Pause 1 second after each major action before narrating the result
- If an API call takes time, fill with "...and here we can see..." rather than dead air
- Practice each ACT independently 3 times before a full run-through
- Do at least 2 full run-throughs before the real recording
