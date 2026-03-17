import {
  createAgent,
  createCronTask,
  deleteAgent,
  deleteCronTask,
  exportAgentUrl,
  getAgent,
  getAgentMemory,
  getAgentProcedures,
  getAgentSession,
  getGlobalProcedures,
  getGlobalUserMemory,
  getMetricsSummary,
  getSetupStatus,
  importAgent,
  listAgentSessions,
  listAgents,
  listMcpServers,
  listCronTasks,
  listGlobalKnowledge,
  restartAgent,
  startAgent,
  startBridge,
  stopAgent,
  stopBridge,
  testAgent,
  updateAgent,
  updateAgentMemory,
  updateAgentProcedures,
  updateCronTask,
  updateGlobalProcedures,
  updateGlobalUserMemory,
} from "./api.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function parseMcpServers(raw) {
  const value = String(raw || "").trim();
  if (!value) {
    return ["*"];
  }
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseDmAllowlist(raw) {
  const value = String(raw || "").trim();
  if (!value) {
    return [];
  }
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function stateClass(state) {
  return String(state || "").toLowerCase();
}

function bridgeStatusDetails(bridge) {
  if (!bridge || !bridge.space_id) {
    return { label: "not configured", className: "warn", canStart: false, canStop: false };
  }
  if (!bridge.bridge_enabled) {
    return { label: "disabled", className: "stopped", canStart: false, canStop: false };
  }
  if (bridge.is_running) {
    return { label: "running", className: "ok", canStart: false, canStop: true };
  }
  return { label: "stopped", className: "error", canStart: true, canStop: false };
}

function bridgeTableMarkup(agents, bridgeByAgent) {
  if (!agents.length) {
    return "<p class='empty'>No agents available.</p>";
  }

  const rows = agents
    .map((agent) => {
      const bridge = bridgeByAgent.get(agent.id) || {
        agent_id: agent.id,
        space_id: agent.space_id || null,
        bridge_enabled: agent.bridge_enabled || false,
        is_running: agent.bridge_running || false,
      };
      const status = bridgeStatusDetails(bridge);
      return `
        <tr>
          <td>${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</td>
          <td><code>${escapeHtml(bridge.space_id || "(not set)")}</code></td>
          <td><span class="status-pill ${escapeHtml(status.className)}"><span class="status-dot"></span>${escapeHtml(status.label)}</span></td>
          <td class="bridge-controls">
            <button class="btn btn-secondary" data-action="bridge-start" data-agent-id="${escapeHtml(agent.id)}" ${status.canStart ? "" : "disabled"}>Start</button>
            <button class="btn btn-secondary" data-action="bridge-stop" data-agent-id="${escapeHtml(agent.id)}" ${status.canStop ? "" : "disabled"}>Stop</button>
          </td>
        </tr>
      `;
    })
    .join("");

  return `
    <table class="bridge-table">
      <thead>
        <tr>
          <th>Agent</th>
          <th>Space</th>
          <th>Status</th>
          <th>Controls</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function tabButtonMarkup(tab, activeTab, label) {
  const active = tab === activeTab ? "active" : "";
  return `<button class="tab-btn ${active}" data-tab="${tab}">${escapeHtml(label)}</button>`;
}

export async function render(root, { onSetupChange }) {
  const STATUS_POLL_INTERVAL_MS = 4000;
  const UPTIME_TICK_INTERVAL_MS = 1000;

  let disposed = false;
  let notice = { tone: "info", text: "Manage active agents, memory, and bridge lifecycle." };

  let activeAgentId = null;
  let activeTab = "persona";

  const detailCache = {};
  const memoryCache = {};
  const proceduresCache = {};
  const sessionsCache = {};
  const transcriptCache = {};
  const cronsCache = {};
  const pendingLifecycle = {};

  let availableMcpServers = null;
  let pollIntervalId = null;
  let metricsIntervalId = null;
  let uptimeIntervalId = null;
  let rerenderInFlight = null;
  let rerenderQueued = false;
  let metricsCache = new Map();

  let globalUserMemory = "";
  let globalProcedures = "";
  let globalKnowledge = [];

  function setNotice(tone, text) {
    notice = { tone, text };
  }

  function clearStatusPoll() {
    if (pollIntervalId !== null) {
      window.clearInterval(pollIntervalId);
      pollIntervalId = null;
    }
  }

  function clearUptimeTicker() {
    if (uptimeIntervalId !== null) {
      window.clearInterval(uptimeIntervalId);
      uptimeIntervalId = null;
    }
  }

  function isUptimeRunning(state) {
    const value = String(state || "").toLowerCase();
    return !["stopped", "dead", "failed", "canceled", "error"].includes(value);
  }

  function lifecycleStatus(agentId, fallbackState) {
    const pending = pendingLifecycle[agentId];
    if (pending === "start" || pending === "restart") {
      return { state: "starting", className: "starting", pending };
    }
    if (pending === "stop") {
      return { state: "stopping", className: "starting", pending };
    }
    return {
      state: String(fallbackState || ""),
      className: stateClass(fallbackState),
      pending: null,
    };
  }

  function startUptimeTicker() {
    clearUptimeTicker();

    uptimeIntervalId = window.setInterval(() => {
      if (disposed) {
        clearUptimeTicker();
        return;
      }
      for (const node of root.querySelectorAll("[data-uptime-for]")) {
        if (node.dataset.running !== "1") {
          continue;
        }
        const current = Number(node.dataset.uptimeS || "0");
        const next = current + 1;
        node.dataset.uptimeS = String(next);
        node.textContent = `${next}s`;
      }
    }, UPTIME_TICK_INTERVAL_MS);
  }

  async function queueRerender(force = false) {
    if (disposed) {
      return;
    }

    const ae = document.activeElement;
    if (!force && ae && root.contains(ae) && ["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName)) {
      rerenderQueued = true;
      return;
    }

    if (rerenderInFlight) {
      rerenderQueued = true;
      return rerenderInFlight;
    }

    rerenderInFlight = (async () => {
      do {
        rerenderQueued = false;
        await rerender();
      } while (rerenderQueued && !disposed);
    })();

    try {
      await rerenderInFlight;
    } finally {
      rerenderInFlight = null;
    }
  }

  async function ensureAgentDetail(agentId) {
    if (!agentId) {
      return null;
    }
    if (!detailCache[agentId]) {
      detailCache[agentId] = await getAgent(agentId);
    }
    return detailCache[agentId];
  }

  async function ensureGlobalMemory() {
    if (!globalUserMemory) {
      const payload = await getGlobalUserMemory();
      globalUserMemory = payload.content || "";
    }
    if (!globalProcedures) {
      const payload = await getGlobalProcedures();
      globalProcedures = payload.content || "";
    }
    if (!globalKnowledge.length) {
      const payload = await listGlobalKnowledge();
      globalKnowledge = payload.items || [];
    }
  }

  async function ensureAvailableMcpServers() {
    if (availableMcpServers === null) {
      try {
        const payload = await listMcpServers();
        availableMcpServers = payload.servers || [];
      } catch (_err) {
        availableMcpServers = [];
      }
    }
    return availableMcpServers;
  }

  function getActiveAgent(agents) {
    if (!agents.length) {
      activeAgentId = null;
      return null;
    }
    if (!activeAgentId || !agents.some((item) => item.id === activeAgentId)) {
      activeAgentId = agents[0].id;
      activeTab = "persona";
    }
    return agents.find((item) => item.id === activeAgentId) || agents[0];
  }

  function tankGridMarkup(agents, metricsMap) {
    return agents
      .map((agent) => {
        const active = agent.id === activeAgentId ? "active" : "";
        const displayStatus = lifecycleStatus(agent.id, agent.state);
        const m = metricsMap.get(agent.id) || {};
        const sessions = m.sessions_total ?? "—";
        const procedures = m.procedures_count ?? "—";
        const avgResp = m.avg_response_s != null ? `${m.avg_response_s}s` : "—";

        const isRunning = !["stopped", "dead", "failed", "canceled", "error"].includes(
          String(displayStatus.state).toLowerCase()
        );
        const statusDotClass = isRunning ? "dot-active" : "dot-idle";

        const pending = displayStatus.pending;
        const actionDisabled = pending ? "disabled" : "";
        const toggleLabel = isRunning ? "Stop" : "Start";
        const toggleAction = isRunning ? "stop" : "start";

        return `
          <div class="tank-card ${active}" data-agent-id="${escapeHtml(agent.id)}">
            <div class="tank-card-header">
              <span class="tank-card-name">${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</span>
              <span class="tank-card-dot ${statusDotClass}"></span>
              <span class="tank-card-state">${escapeHtml(displayStatus.state)}</span>
            </div>
            <div class="tank-card-metrics">
              <div class="tank-metric"><span class="tank-metric-val">${escapeHtml(String(sessions))}</span> sessions</div>
              <div class="tank-metric"><span class="tank-metric-val">${escapeHtml(String(procedures))}</span> procedures</div>
              <div class="tank-metric"><span class="tank-metric-val">${escapeHtml(String(avgResp))}</span> avg</div>
            </div>
            <div class="tank-card-actions">
              <button class="btn btn-secondary btn-sm" data-action="select-agent" data-agent-id="${escapeHtml(agent.id)}">View</button>
              <button class="btn btn-secondary btn-sm" data-action="${toggleAction}" data-agent-id="${escapeHtml(agent.id)}" ${actionDisabled}>${escapeHtml(toggleLabel)}</button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function activeHeroMarkup(agent) {
    const displayStatus = lifecycleStatus(agent.id, agent.state);
    const pending = displayStatus.pending;
    const actionDisabled = pending ? "disabled" : "";
    const uptime = Number(agent.uptime_s || 0);
    const uptimeRunning = isUptimeRunning(displayStatus.state) ? "1" : "0";

    return `
      <section class="active-agent-hero">
        <div>
          <div class="eyebrow">Active Agent</div>
          <h2>${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</h2>
          <div class="agent-meta">id: ${escapeHtml(agent.id)} · model: ${escapeHtml(agent.model)} · uptime: <span data-uptime-for="${escapeHtml(agent.id)}" data-uptime-s="${escapeHtml(uptime)}" data-running="${uptimeRunning}">${escapeHtml(uptime)}s</span></div>
        </div>
        <div class="actions">
          <span class="status-pill ${escapeHtml(displayStatus.className)}"><span class="status-dot"></span>${escapeHtml(displayStatus.state)}</span>
          <button class="btn btn-secondary" data-action="start" data-agent-id="${escapeHtml(agent.id)}" ${actionDisabled}>${pending === "start" ? "Starting..." : "Start"}</button>
          <button class="btn btn-secondary" data-action="stop" data-agent-id="${escapeHtml(agent.id)}" ${actionDisabled}>${pending === "stop" ? "Stopping..." : "Stop"}</button>
          <button class="btn btn-secondary" data-action="restart" data-agent-id="${escapeHtml(agent.id)}" ${actionDisabled}>${pending === "restart" ? "Restarting..." : "Restart"}</button>
          <button class="btn btn-secondary" data-action="test" data-agent-id="${escapeHtml(agent.id)}" ${actionDisabled}>Send Test</button>
          <a class="btn btn-secondary" href="${exportAgentUrl(agent.id)}" download="${escapeHtml(agent.id)}.g3agent">Export</a>
          <button class="btn btn-danger" data-action="delete" data-agent-id="${escapeHtml(agent.id)}" ${actionDisabled}>Delete</button>
        </div>
      </section>
    `;
  }

  function personaTabMarkup(agent, detail, mcpServers) {
    return `
      <form class="persona-form" data-agent-id="${escapeHtml(agent.id)}">
        <div class="form-grid">
          <div class="field">
            <label>Name</label>
            <input name="name" value="${escapeHtml(detail.name || "")}" />
          </div>
          <div class="field">
            <label>Emoji</label>
            <input name="emoji" value="${escapeHtml(detail.emoji || "🤖")}" />
          </div>
          <div class="field">
            <label>Model</label>
            <input name="model" value="${escapeHtml(detail.model || "gemini")}" />
          </div>
          <div class="field">
            <label>MCP Servers</label>
            ${
              mcpServers && mcpServers.length
                ? `<div class="mcp-checklist">
                    ${mcpServers.map((srv) => {
                      const checked = (detail.mcp_servers || ["*"]).includes("*") || (detail.mcp_servers || []).includes(srv) ? "checked" : "";
                      return `<label class="mcp-option"><input type="checkbox" name="mcp_server_item" value="${escapeHtml(srv)}" ${checked} /> ${escapeHtml(srv)}</label>`;
                    }).join("")}
                  </div>
                  <label class="mcp-option"><input type="checkbox" name="mcp_server_wildcard" ${(detail.mcp_servers || ["*"]).includes("*") ? "checked" : ""} /> * (all servers)</label>`
                : `<input name="mcp_servers" value="${escapeHtml((detail.mcp_servers || ["*"]).join(", "))}" />`
            }
          </div>
        </div>
        <div class="field">
          <label>SOUL.md</label>
          <textarea name="soul">${escapeHtml(detail.soul || "")}</textarea>
        </div>
        <div class="form-grid">
          <div class="field">
            <label>Space ID</label>
            <input name="space_id" value="${escapeHtml(detail.space_id || "")}" placeholder="spaces/AAAA..." />
          </div>
          <div class="field">
            <label>Bridge Enabled</label>
            <select name="bridge_enabled">
              <option value="true" ${detail.bridge_enabled ? "selected" : ""}>true</option>
              <option value="false" ${detail.bridge_enabled ? "" : "selected"}>false</option>
            </select>
          </div>
          <div class="field">
            <label>Enabled</label>
            <select name="enabled">
              <option value="true" ${detail.enabled ? "selected" : ""}>true</option>
              <option value="false" ${detail.enabled ? "" : "selected"}>false</option>
            </select>
          </div>
          <div class="field">
            <label>Heartbeat Enabled</label>
            <select name="heartbeat_enabled">
              <option value="true" ${detail.heartbeat_enabled !== false ? "selected" : ""}>true</option>
              <option value="false" ${detail.heartbeat_enabled === false ? "selected" : ""}>false</option>
            </select>
          </div>
          <div class="field">
            <label>Heartbeat Interval (s)</label>
            <input name="heartbeat_interval_s" type="number" min="1" step="1" value="${escapeHtml(detail.heartbeat_interval_s || 300)}" />
          </div>
        </div>
        <div class="field">
          <label>DM Allowlist (one sender ID per line)</label>
          <textarea name="dm_allowlist" placeholder="users/abc123&#10;user@example.com">${escapeHtml((detail.dm_allowlist || []).join("\n"))}</textarea>
        </div>
        <div class="actions">
          <button class="btn btn-primary" type="submit">Save Persona</button>
        </div>
      </form>
    `;
  }

  function memoryTabMarkup(agent) {
    return `
      <div class="field">
        <label>Agent Memory (MEMORY.md)</label>
        <textarea data-memory-for="${escapeHtml(agent.id)}">${escapeHtml(memoryCache[agent.id] || "")}</textarea>
      </div>
      <div class="actions">
        <button class="btn btn-secondary" data-action="load-memory" data-agent-id="${escapeHtml(agent.id)}">Load Memory</button>
        <button class="btn btn-primary" data-action="save-memory" data-agent-id="${escapeHtml(agent.id)}">Save Memory</button>
      </div>
    `;
  }

  function proceduresTabMarkup(agent) {
    return `
      <div class="field">
        <label>Agent Procedures (PROCEDURES.md)</label>
        <textarea data-procedures-for="${escapeHtml(agent.id)}">${escapeHtml(proceduresCache[agent.id] || "")}</textarea>
      </div>
      <div class="actions">
        <button class="btn btn-secondary" data-action="load-procedures" data-agent-id="${escapeHtml(agent.id)}">Load Procedures</button>
        <button class="btn btn-primary" data-action="save-procedures" data-agent-id="${escapeHtml(agent.id)}">Save Procedures</button>
      </div>
    `;
  }

  function renderMessageContent(role, content) {
    if (role === "assistant" && typeof window.marked !== "undefined" && typeof window.DOMPurify !== "undefined") {
      const html = window.marked.parse(String(content || ""));
      return window.DOMPurify.sanitize(html);
    }
    return `<p>${escapeHtml(String(content || ""))}</p>`;
  }

  function renderTranscript(cached) {
    if (!cached || typeof cached !== "object") {
      return "<p class='empty'>(select a session)</p>";
    }
    const entries = Array.isArray(cached.entries) ? cached.entries : [];
    const messages = entries.filter((e) => e && e.type === "message" && e.message);
    if (!messages.length) {
      return "<p class='empty'>(no messages)</p>";
    }
    return messages
      .map((e) => {
        const role = String(e.message.role || "unknown");
        const content = e.message.content || "";
        const ts = e.timestamp ? `<span class="msg-ts">${escapeHtml(e.timestamp.replace("T", " ").slice(0, 19))}</span>` : "";
        return `<div class="msg msg-${escapeHtml(role)}">
          <div class="msg-header"><span class="msg-role">${escapeHtml(role)}</span>${ts}</div>
          <div class="msg-body">${renderMessageContent(role, content)}</div>
        </div>`;
      })
      .join("");
  }

  function sessionsTabMarkup(agent) {
    const sessions = sessionsCache[agent.id] || [];
    const sessionOptions = sessions.length
      ? sessions.map((sid) => `<option value="${escapeHtml(sid)}">${escapeHtml(sid)}</option>`).join("")
      : "<option value=''>No sessions</option>";

    return `
      <div class="form-grid">
        <div class="field">
          <label>Sessions</label>
          <select data-sessions-for="${escapeHtml(agent.id)}">${sessionOptions}</select>
        </div>
      </div>
      <div class="actions">
        <button class="btn btn-secondary" data-action="load-sessions" data-agent-id="${escapeHtml(agent.id)}">Refresh Sessions</button>
        <button class="btn btn-secondary" data-action="load-session" data-agent-id="${escapeHtml(agent.id)}">Open Session</button>
      </div>
      <div class="chat-transcript">${renderTranscript(transcriptCache[agent.id])}</div>
    `;
  }

  function cronsTabMarkup(agent) {
    const tasks = cronsCache[agent.id] || [];
    const taskRows = tasks.length
      ? tasks.map((t) => {
          const status = t.enabled ? "✅" : "⏸";
          const last = t.last_run ? t.last_run.slice(0, 19).replace("T", " ") : "never";
          return `
            <tr>
              <td>${status}</td>
              <td><code>${escapeHtml(t.id.slice(0, 8))}</code></td>
              <td><code>${escapeHtml(t.schedule)}</code></td>
              <td>${escapeHtml(t.instruction)}</td>
              <td>${escapeHtml(last)}</td>
              <td class="actions">
                <button class="btn btn-secondary" data-action="cron-toggle" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(t.id)}" data-enabled="${t.enabled ? "1" : "0"}">${t.enabled ? "Disable" : "Enable"}</button>
                <button class="btn btn-danger" data-action="cron-delete" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(t.id)}">Delete</button>
              </td>
            </tr>`;
        }).join("")
      : `<tr><td colspan="6" class="empty">No cron tasks. Add one below.</td></tr>`;

    return `
      <div class="actions">
        <button class="btn btn-secondary" data-action="load-crons" data-agent-id="${escapeHtml(agent.id)}">Refresh</button>
      </div>
      <table class="cron-table">
        <thead><tr><th></th><th>ID</th><th>Schedule</th><th>Instruction</th><th>Last Run</th><th></th></tr></thead>
        <tbody>${taskRows}</tbody>
      </table>
      <form class="cron-add-form" data-agent-id="${escapeHtml(agent.id)}">
        <div class="form-grid">
          <div class="field">
            <label>Schedule (cron)</label>
            <input name="schedule" placeholder="0 9 * * *" required />
          </div>
          <div class="field" style="grid-column: 1 / -1;">
            <label>Instruction</label>
            <input name="instruction" placeholder="Send morning briefing" required />
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-primary" type="submit">Add Cron Task</button>
        </div>
      </form>
    `;
  }

  // --- Live Thinking ---
  let thinkingEventSource = null;
  let thinkingAgentId = null;
  const thinkingEvents = {};

  function destroyThinkingStream() {
    if (thinkingEventSource) {
      thinkingEventSource.close();
      thinkingEventSource = null;
      thinkingAgentId = null;
    }
  }

  function connectThinkingStream(agentId) {
    if (thinkingAgentId === agentId && thinkingEventSource) {
      return;
    }
    destroyThinkingStream();
    thinkingAgentId = agentId;
    if (!thinkingEvents[agentId]) {
      thinkingEvents[agentId] = [];
    }

    const es = new EventSource(`/agents/${encodeURIComponent(agentId)}/stream`);
    thinkingEventSource = es;

    es.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        thinkingEvents[agentId].push(event);
        // Keep max 200 events
        if (thinkingEvents[agentId].length > 200) {
          thinkingEvents[agentId] = thinkingEvents[agentId].slice(-150);
        }
        appendThinkingEvent(event);
      } catch (_err) {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      updateConnectionPill(false);
    };

    es.onopen = () => {
      updateConnectionPill(true);
    };
  }

  function updateConnectionPill(connected) {
    const pill = root.querySelector(".thinking-connection-pill");
    if (pill) {
      pill.textContent = connected ? "connected" : "disconnected";
      pill.className = `thinking-connection-pill status-pill ${connected ? "ok" : "error"}`;
    }
  }

  function renderThinkingEventHtml(event) {
    const type = event.type || "unknown";
    if (type === "user_input") {
      const sender = escapeHtml(event.sender || "user");
      const text = escapeHtml(event.text || "");
      return `<div class="thinking-divider"><hr /><span>new message from ${sender}</span></div>
              <div class="thinking-block thinking-user"><span class="thinking-label">user</span> ${text}</div>`;
    }
    if (type === "message") {
      const content = escapeHtml(event.text || event.data?.content || "");
      if (!content) return "";
      return `<div class="thinking-block thinking-thought"><span class="thinking-label">thinking</span> ${content}</div>`;
    }
    if (type === "tool_use") {
      const toolName = escapeHtml(event.data?.tool_name || event.data?.toolName || event.data?.name || "tool");
      return `<div class="thinking-block thinking-tool"><span class="thinking-label">tool</span> <span class="tool-name">${toolName}</span> <span class="thinking-status breathing-dot">running</span></div>`;
    }
    if (type === "tool_result") {
      const toolName = escapeHtml(event.data?.tool_name || event.data?.toolName || event.data?.name || "tool");
      return `<div class="thinking-block thinking-tool done"><span class="thinking-label">tool</span> <span class="tool-name">${toolName}</span> <span class="thinking-status done-check">done</span></div>`;
    }
    if (type === "response") {
      const text = event.text || "";
      const rendered = typeof window.marked !== "undefined" && typeof window.DOMPurify !== "undefined"
        ? window.DOMPurify.sanitize(window.marked.parse(String(text)))
        : `<p>${escapeHtml(text)}</p>`;
      return `<div class="thinking-block thinking-response"><span class="thinking-label">response</span> <div class="thinking-response-body">${rendered}</div></div>`;
    }
    if (type === "heartbeat_review") {
      const summary = escapeHtml(event.summary || "Heartbeat review generated.");
      const suggestions = Array.isArray(event.suggestions) ? event.suggestions : [];
      const stats = event.stats && typeof event.stats === "object" ? event.stats : {};
      const statsText = Object.keys(stats).length
        ? `<p>stats: pending=${escapeHtml(String(stats.pending ?? 0))}, in_progress=${escapeHtml(String(stats.in_progress ?? 0))}, blocked=${escapeHtml(String(stats.blocked ?? 0))}, overdue=${escapeHtml(String(stats.overdue ?? 0))}</p>`
        : "";
      const suggestionsHtml = suggestions.length
        ? `<ul>${suggestions
            .map((item) => {
              const category = escapeHtml(item.category || "suggestion");
              const severity = escapeHtml(item.severity || "info");
              const message = escapeHtml(item.message || "");
              return `<li><strong>${category}</strong> [${severity}] ${message}</li>`;
            })
            .join("")}</ul>`
        : "<p>No suggestions.</p>";
      return `<div class="thinking-block thinking-response"><span class="thinking-label">heartbeat</span> <div class="thinking-response-body"><p>${summary}</p>${statsText}${suggestionsHtml}</div></div>`;
    }
    if (type === "error") {
      const msg = escapeHtml(event.data?.message || event.text || "error");
      return `<div class="thinking-block thinking-error"><span class="thinking-label">error</span> ${msg}</div>`;
    }
    return "";
  }

  function appendThinkingEvent(event) {
    const container = root.querySelector(".thinking-stream");
    if (!container) return;
    const html = renderThinkingEventHtml(event);
    if (!html) return;

    // If this is a tool_result, try to update the last matching tool_use block
    if (event.type === "tool_result") {
      const toolBlocks = container.querySelectorAll(".thinking-tool:not(.done)");
      if (toolBlocks.length > 0) {
        const last = toolBlocks[toolBlocks.length - 1];
        last.classList.add("done");
        const statusEl = last.querySelector(".thinking-status");
        if (statusEl) {
          statusEl.className = "thinking-status done-check";
          statusEl.textContent = "done";
        }
        return;
      }
    }

    const div = document.createElement("div");
    div.innerHTML = html;
    while (div.firstChild) {
      container.appendChild(div.firstChild);
    }
    container.scrollTop = container.scrollHeight;
  }

  function thinkingTabMarkup(agent) {
    const events = thinkingEvents[agent.id] || [];
    const eventsHtml = events.map(renderThinkingEventHtml).filter(Boolean).join("");

    return `
      <div class="thinking-panel-header">
        <span class="thinking-title">LIVE THINKING: ${escapeHtml(agent.name)}</span>
        <span class="thinking-connection-pill status-pill ok">connected</span>
      </div>
      <div class="thinking-stream">${eventsHtml || '<p class="empty">Waiting for events...</p>'}</div>
      <div class="actions">
        <button class="btn btn-secondary" data-action="clear-thinking" data-agent-id="${escapeHtml(agent.id)}">Clear</button>
      </div>
    `;
  }

  function tabPanelMarkup(agent, detail) {
    if (activeTab === "thinking") {
      return thinkingTabMarkup(agent);
    }
    if (activeTab === "memory") {
      return memoryTabMarkup(agent);
    }
    if (activeTab === "procedures") {
      return proceduresTabMarkup(agent);
    }
    if (activeTab === "sessions") {
      return sessionsTabMarkup(agent);
    }
    if (activeTab === "crons") {
      return cronsTabMarkup(agent);
    }
    return personaTabMarkup(agent, detail, availableMcpServers || []);
  }

  async function rerender() {
    if (disposed) {
      return;
    }

    let setup;
    let agents;
    try {
      [setup, agents] = await Promise.all([getSetupStatus(), listAgents()]);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      root.innerHTML = `<div class='notice error'>Failed to load agents view: ${escapeHtml(message)}</div>`;
      return;
    }

    try {
      const summary = await getMetricsSummary();
      const newMap = new Map();
      for (const entry of summary.agents || []) {
        newMap.set(entry.agent_id, entry);
      }
      metricsCache = newMap;
    } catch (_err) {
      // Keep existing cache on failure
    }

    const activeAgent = getActiveAgent(agents);
    if (activeAgent) {
      try {
        detailCache[activeAgent.id] = await ensureAgentDetail(activeAgent.id);
      } catch (_err) {
        detailCache[activeAgent.id] = detailCache[activeAgent.id] || activeAgent;
      }
    }
    await ensureAvailableMcpServers();

    const bridgeByAgent = new Map((setup.agent_bridges || []).map((item) => [item.agent_id, item]));
    const runningBridgeCount = Array.from(bridgeByAgent.values()).filter((item) => item.is_running).length;
    const bridgeLabel = runningBridgeCount > 0 ? "running" : "stopped";
    const bridgeClass = runningBridgeCount > 0 ? "ok" : "error";
    const detail = activeAgent ? detailCache[activeAgent.id] || activeAgent : null;

    root.innerHTML = `
      <div class="view-stack">
        ${notice?.text ? `<div class="notice ${notice.tone}">${escapeHtml(notice.text)}</div>` : ""}

        <div class="step-panel">
          <h2>Bridge Status</h2>
          <div class="actions">
            <span class="status-pill ${bridgeClass}">${escapeHtml(bridgeLabel)}</span>
            <span class="agent-meta">${escapeHtml(String(runningBridgeCount))}/${escapeHtml(String(agents.length))} running</span>
          </div>
          ${bridgeTableMarkup(agents, bridgeByAgent)}
          <div class="actions">
            <button class="btn btn-primary" data-action="bridge-start-all">Start All Bridges</button>
            <button class="btn btn-secondary" data-action="bridge-stop-all">Stop All Bridges</button>
          </div>
        </div>

        <div class="step-panel">
          <h2>Add Agent</h2>
          <form id="create-agent-form" class="form-grid">
            <div class="field">
              <label>Name</label>
              <input name="name" placeholder="Iris" required />
            </div>
            <div class="field">
              <label>Emoji</label>
              <input name="emoji" value="🤖" />
            </div>
            <div class="field">
              <label>Model</label>
              <input name="model" value="gemini" />
            </div>
            <div class="field">
              <label>MCP Servers</label>
              <input name="mcp_servers" value="*" />
            </div>
            <div class="field">
              <label>Space ID</label>
              <input name="space_id" placeholder="spaces/AAAA..." value="${escapeHtml(setup.space_id || "")}" />
            </div>
            <div class="field">
              <label>Bridge Enabled</label>
              <select name="bridge_enabled">
                <option value="true" ${setup.space_id ? "selected" : ""}>true</option>
                <option value="false" ${setup.space_id ? "" : "selected"}>false</option>
              </select>
            </div>
            <div class="field" style="grid-column: 1 / -1;">
              <label>SOUL.md</label>
              <textarea name="soul" placeholder="Persona and tone"></textarea>
            </div>
            <div class="actions" style="grid-column: 1 / -1;">
              <button class="btn btn-primary" type="submit">Create Agent</button>
              <input type="file" id="import-agent-file" accept=".g3agent,.zip" style="display:none" />
              <button class="btn btn-secondary" type="button" data-action="import-agent">Import Agent</button>
            </div>
          </form>
        </div>

        <div class="step-panel">
          <h2>Global Memory</h2>
          <div class="field">
            <label>User Memory (data/.memory/USER.md)</label>
            <textarea id="global-user-memory">${escapeHtml(globalUserMemory)}</textarea>
          </div>
          <div class="field">
            <label>Global Procedures (data/.memory/PROCEDURES.md)</label>
            <textarea id="global-procedures">${escapeHtml(globalProcedures)}</textarea>
          </div>
          <div class="actions">
            <button class="btn btn-secondary" data-action="load-global-memory">Reload Global Memory</button>
            <button class="btn btn-primary" data-action="save-global-memory">Save Global Memory</button>
          </div>
          <p class="agent-meta">Knowledge files: ${escapeHtml(globalKnowledge.join(", ") || "(none)")}</p>
        </div>

        <div class="step-panel">
          <h2>Lobster Tank</h2>
          ${
            agents.length
              ? `<div class="tank-grid">${tankGridMarkup(agents, metricsCache)}</div>`
              : "<p class='empty'>No agents yet.</p>"
          }
        </div>

        ${
          activeAgent && detail
            ? `
              ${activeHeroMarkup(activeAgent)}
              <div class="step-panel">
                <div class="agent-tabs">
                  ${tabButtonMarkup("persona", activeTab, "Persona")}
                  ${tabButtonMarkup("thinking", activeTab, "Live Thinking")}
                  ${tabButtonMarkup("memory", activeTab, "Memory")}
                  ${tabButtonMarkup("procedures", activeTab, "Procedures")}
                  ${tabButtonMarkup("sessions", activeTab, "Sessions")}
                  ${tabButtonMarkup("crons", activeTab, "Crons")}
                </div>
                <div class="tab-panel">${tabPanelMarkup(activeAgent, detail)}</div>
              </div>
            `
            : ""
        }
      </div>
    `;

    startUptimeTicker();

    for (const header of root.querySelectorAll(".collapsible-header")) {
      header.addEventListener("click", () => {
        const expanded = header.getAttribute("aria-expanded") === "true";
        header.setAttribute("aria-expanded", String(!expanded));
        const body = header.nextElementSibling;
        if (body && body.classList.contains("collapsible-body")) {
          body.classList.toggle("open", !expanded);
        }
      });
    }

    root.querySelector("#create-agent-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const name = String(data.get("name") || "").trim();
      if (!name) {
        setNotice("error", "Agent name is required.");
        queueRerender(true);
        return;
      }

      try {
        const created = await createAgent({
          name,
          emoji: String(data.get("emoji") || "🤖").trim() || "🤖",
          model: String(data.get("model") || "gemini").trim() || "gemini",
          mcp_servers: parseMcpServers(data.get("mcp_servers")),
          soul: String(data.get("soul") || ""),
          space_id: String(data.get("space_id") || "").trim() || null,
          bridge_enabled: String(data.get("bridge_enabled") || "false") === "true",
        });
        activeAgentId = created.id;
        setNotice("success", `Agent ${name} created.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to create agent: ${message}`);
      }
      queueRerender(true);
    });

    // Connect SSE stream when Live Thinking tab is active
    if (activeTab === "thinking" && activeAgent) {
      connectThinkingStream(activeAgent.id);
      // Scroll to bottom after render
      const stream = root.querySelector(".thinking-stream");
      if (stream) stream.scrollTop = stream.scrollHeight;
    } else {
      destroyThinkingStream();
    }

    for (const tabButton of root.querySelectorAll("button[data-tab]")) {
      tabButton.addEventListener("click", () => {
        activeTab = tabButton.dataset.tab || "persona";
        queueRerender();
      });
    }

    for (const header of root.querySelectorAll(".collapsible-header")) {
      header.addEventListener("click", () => {
        const expanded = header.getAttribute("aria-expanded") === "true";
        header.setAttribute("aria-expanded", String(!expanded));
        const body = header.nextElementSibling;
        if (body && body.classList.contains("collapsible-body")) {
          body.classList.toggle("open", !expanded);
        }
      });
    }

    for (const button of root.querySelectorAll("button[data-action]")) {
      button.addEventListener("click", async () => {
        const action = button.dataset.action;
        const agentId = button.dataset.agentId;

        if (action === "select-agent") {
          activeAgentId = agentId || null;
          activeTab = "persona";
          queueRerender();
          return;
        }

        try {
          if (action === "load-global-memory") {
            globalUserMemory = "";
            globalProcedures = "";
            globalKnowledge = [];
            await ensureGlobalMemory();
            setNotice("info", "Reloaded global memory.");
          } else if (action === "save-global-memory") {
            const userValue = root.querySelector("#global-user-memory")?.value ?? "";
            const proceduresValue = root.querySelector("#global-procedures")?.value ?? "";
            await Promise.all([updateGlobalUserMemory(userValue), updateGlobalProcedures(proceduresValue)]);
            globalUserMemory = userValue;
            globalProcedures = proceduresValue;
            setNotice("success", "Saved global memory and procedures.");
          } else if (action === "bridge-start-all") {
            await startBridge();
            setNotice("success", "Started all configured bridges.");
            await onSetupChange();
          } else if (action === "bridge-stop-all") {
            await stopBridge();
            setNotice("info", "Stopped all bridges.");
            await onSetupChange();
          } else if (action === "bridge-start" && agentId) {
            await startBridge(agentId);
            setNotice("success", `Started bridge for ${agentId}.`);
            await onSetupChange();
          } else if (action === "bridge-stop" && agentId) {
            await stopBridge(agentId);
            setNotice("info", `Stopped bridge for ${agentId}.`);
            await onSetupChange();
          } else if (!action || !agentId) {
            return;
          } else if (action === "start") {
            pendingLifecycle[agentId] = action;
            await queueRerender();
            try {
              await startAgent(agentId);
              delete detailCache[agentId];
              setNotice("success", `Started ${agentId}.`);
            } finally {
              delete pendingLifecycle[agentId];
            }
          } else if (action === "stop") {
            pendingLifecycle[agentId] = action;
            await queueRerender();
            try {
              await stopAgent(agentId);
              delete detailCache[agentId];
              setNotice("info", `Stopped ${agentId}.`);
            } finally {
              delete pendingLifecycle[agentId];
            }
          } else if (action === "restart") {
            pendingLifecycle[agentId] = action;
            await queueRerender();
            try {
              await restartAgent(agentId);
              delete detailCache[agentId];
              setNotice("success", `Restarted ${agentId}.`);
            } finally {
              delete pendingLifecycle[agentId];
            }
          } else if (action === "delete") {
            await deleteAgent(agentId);
            delete detailCache[agentId];
            delete memoryCache[agentId];
            delete proceduresCache[agentId];
            delete sessionsCache[agentId];
            delete transcriptCache[agentId];
            delete cronsCache[agentId];
            if (activeAgentId === agentId) {
              activeAgentId = null;
            }
            setNotice("info", `Deleted ${agentId}.`);
          } else if (action === "test") {
            await testAgent(agentId, "management panel test");
            setNotice("success", `Sent test message for ${agentId}.`);
          } else if (action === "load-memory") {
            const payload = await getAgentMemory(agentId);
            memoryCache[agentId] = payload.content || "";
            setNotice("info", `Loaded memory for ${agentId}.`);
          } else if (action === "save-memory") {
            const area = root.querySelector(`textarea[data-memory-for='${CSS.escape(agentId)}']`);
            const content = area?.value ?? "";
            await updateAgentMemory(agentId, content);
            memoryCache[agentId] = content;
            setNotice("success", `Saved memory for ${agentId}.`);
          } else if (action === "load-procedures") {
            const payload = await getAgentProcedures(agentId);
            proceduresCache[agentId] = payload.content || "";
            setNotice("info", `Loaded procedures for ${agentId}.`);
          } else if (action === "save-procedures") {
            const area = root.querySelector(`textarea[data-procedures-for='${CSS.escape(agentId)}']`);
            const content = area?.value ?? "";
            await updateAgentProcedures(agentId, content);
            proceduresCache[agentId] = content;
            setNotice("success", `Saved procedures for ${agentId}.`);
          } else if (action === "load-sessions") {
            const payload = await listAgentSessions(agentId);
            sessionsCache[agentId] = payload.sessions || [];
            setNotice("info", `Loaded sessions for ${agentId}.`);
          } else if (action === "load-session") {
            const select = root.querySelector(`select[data-sessions-for='${CSS.escape(agentId)}']`);
            const sessionId = select?.value;
            if (!sessionId) {
              setNotice("error", "Select a session first.");
            } else {
              const payload = await getAgentSession(agentId, sessionId);
              transcriptCache[agentId] = payload;
              setNotice("info", `Loaded session ${sessionId}.`);
            }
          } else if (action === "load-crons") {
            cronsCache[agentId] = await listCronTasks(agentId);
            setNotice("info", `Loaded cron tasks for ${agentId}.`);
          } else if (action === "cron-toggle") {
            const taskId = button.dataset.taskId;
            const currentlyEnabled = button.dataset.enabled === "1";
            await updateCronTask(agentId, taskId, { enabled: !currentlyEnabled });
            cronsCache[agentId] = await listCronTasks(agentId);
            setNotice("success", `Cron task ${currentlyEnabled ? "disabled" : "enabled"}.`);
          } else if (action === "cron-delete") {
            const taskId = button.dataset.taskId;
            await deleteCronTask(agentId, taskId);
            cronsCache[agentId] = await listCronTasks(agentId);
            setNotice("info", "Cron task deleted.");
          } else if (action === "clear-thinking") {
            thinkingEvents[agentId] = [];
            setNotice("info", "Cleared thinking events.");
          } else if (action === "import-agent") {
            const fileInput = root.querySelector("#import-agent-file");
            if (!fileInput) return;
            fileInput.value = "";
            fileInput.onchange = async () => {
              const file = fileInput.files?.[0];
              if (!file) return;
              try {
                const result = await importAgent(file, false);
                activeAgentId = result.agent_id;
                setNotice("success", `Imported agent "${result.agent_id}" successfully.`);
              } catch (err) {
                if (err.status === 409) {
                  setNotice("error", `Agent already exists. Re-upload with overwrite or rename the agent. (${err.message})`);
                } else {
                  const message = err instanceof Error ? err.message : String(err);
                  setNotice("error", `Import failed: ${message}`);
                }
              }
              queueRerender();
            };
            fileInput.click();
            return;
          }
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setNotice("error", `${action || "action"} failed${agentId ? ` for ${agentId}` : ""}: ${message}`);
        }

        queueRerender();
      });
    }

    for (const form of root.querySelectorAll("form.persona-form")) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const agentId = form.dataset.agentId;
        if (!agentId) {
          return;
        }

        const data = new FormData(form);
        try {
          // Build mcp_servers from checklist (if present) or fallback to text input
          let mcpServersValue;
          const wildcardChecked = form.querySelector("input[name='mcp_server_wildcard']");
          const itemCheckboxes = form.querySelectorAll("input[name='mcp_server_item']:checked");
          if (wildcardChecked !== null) {
            // Checklist mode
            if (wildcardChecked.checked) {
              mcpServersValue = ["*"];
            } else {
              mcpServersValue = Array.from(itemCheckboxes).map((cb) => cb.value).filter(Boolean);
              if (!mcpServersValue.length) {
                mcpServersValue = ["*"];
              }
            }
          } else {
            // Fallback text input mode
            mcpServersValue = parseMcpServers(data.get("mcp_servers"));
          }

          const payload = {
            name: String(data.get("name") || "").trim(),
            emoji: String(data.get("emoji") || "🤖").trim() || "🤖",
            model: String(data.get("model") || "gemini").trim() || "gemini",
            soul: String(data.get("soul") || ""),
            mcp_servers: mcpServersValue,
            enabled: String(data.get("enabled") || "true") === "true",
            dm_allowlist: parseDmAllowlist(data.get("dm_allowlist")),
            space_id: String(data.get("space_id") || "").trim() || null,
            bridge_enabled: String(data.get("bridge_enabled") || "false") === "true",
            heartbeat_enabled: String(data.get("heartbeat_enabled") || "true") === "true",
            heartbeat_interval_s: Math.max(1, Number(data.get("heartbeat_interval_s") || 300)),
          };
          detailCache[agentId] = await updateAgent(agentId, payload);
          setNotice("success", `Updated ${agentId}.`);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setNotice("error", `Failed to update ${agentId}: ${message}`);
        }
        queueRerender(true);
      });
    }

    for (const form of root.querySelectorAll("form.cron-add-form")) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const agentId = form.dataset.agentId;
        if (!agentId) return;
        const data = new FormData(form);
        const schedule = String(data.get("schedule") || "").trim();
        const instruction = String(data.get("instruction") || "").trim();
        if (!schedule || !instruction) {
          setNotice("error", "Schedule and instruction are required.");
          queueRerender(true);
          return;
        }
        try {
          await createCronTask(agentId, schedule, instruction);
          cronsCache[agentId] = await listCronTasks(agentId);
          setNotice("success", "Cron task added.");
          form.reset();
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setNotice("error", `Failed to add cron task: ${message}`);
        }
        queueRerender(true);
      });
    }
  }

  function formFieldForAgent(container, agentId, fieldName) {
    const form = container.querySelector(`form.persona-form[data-agent-id='${CSS.escape(agentId)}']`);
    if (!form) {
      return null;
    }
    return form.querySelector(`[name='${CSS.escape(fieldName)}']`);
  }

  try {
    await ensureGlobalMemory();
  } catch (_err) {
    // Keep UI usable even if global files are not available yet.
  }

  await queueRerender();
  pollIntervalId = window.setInterval(() => {
    queueRerender();
  }, STATUS_POLL_INTERVAL_MS);

  const METRICS_POLL_INTERVAL_MS = 30000;
  metricsIntervalId = window.setInterval(() => {
    if (!disposed) {
      queueRerender();
    }
  }, METRICS_POLL_INTERVAL_MS);

  root.addEventListener("focusout", (e) => {
    setTimeout(() => {
      if (disposed) return;
      const ae = document.activeElement;
      if (!ae || !root.contains(ae) || !["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName)) {
        if (rerenderQueued) {
          queueRerender(true);
        }
      }
    }, 10);
  });

  return {
    destroy() {
      disposed = true;
      clearStatusPoll();
      clearUptimeTicker();
      if (metricsIntervalId !== null) {
        window.clearInterval(metricsIntervalId);
        metricsIntervalId = null;
      }
      destroyThinkingStream();
    },
  };
}
