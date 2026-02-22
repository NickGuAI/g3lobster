import {
  createAgent,
  deleteAgent,
  getAgent,
  getAgentMemory,
  getAgentProcedures,
  getAgentSession,
  getGlobalProcedures,
  getGlobalUserMemory,
  getSetupStatus,
  linkAgentBot,
  listAgentSessions,
  listAgents,
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

function stateClass(state) {
  return String(state || "").toLowerCase();
}

function tabButtonMarkup(tab, activeTab, label) {
  const active = tab === activeTab ? "active" : "";
  return `<button class="tab-btn ${active}" data-tab="${tab}">${escapeHtml(label)}</button>`;
}

export async function render(root, { onSetupChange }) {
  let disposed = false;
  let notice = { tone: "info", text: "Manage active agents, memory, and bridge lifecycle." };

  let activeAgentId = null;
  let activeTab = "persona";

  const detailCache = {};
  const memoryCache = {};
  const proceduresCache = {};
  const sessionsCache = {};
  const transcriptCache = {};

  let globalUserMemory = "";
  let globalProcedures = "";
  let globalKnowledge = [];

  function setNotice(tone, text) {
    notice = { tone, text };
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

  function selectorMarkup(agents) {
    return agents
      .map((agent) => {
        const active = agent.id === activeAgentId ? "active" : "";
        return `
          <button class="agent-chip ${active}" data-action="select-agent" data-agent-id="${escapeHtml(agent.id)}">
            <span class="chip-name">${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</span>
            <span class="status-pill ${stateClass(agent.state)}">${escapeHtml(agent.state)}</span>
          </button>
        `;
      })
      .join("");
  }

  function activeHeroMarkup(agent) {
    return `
      <section class="active-agent-hero">
        <div>
          <div class="eyebrow">Active Agent</div>
          <h2>${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</h2>
          <div class="agent-meta">id: ${escapeHtml(agent.id)} · model: ${escapeHtml(agent.model)} · uptime: ${escapeHtml(agent.uptime_s)}s</div>
        </div>
        <div class="actions">
          <span class="status-pill ${stateClass(agent.state)}">${escapeHtml(agent.state)}</span>
          <button class="btn btn-secondary" data-action="start" data-agent-id="${escapeHtml(agent.id)}">Start</button>
          <button class="btn btn-secondary" data-action="stop" data-agent-id="${escapeHtml(agent.id)}">Stop</button>
          <button class="btn btn-secondary" data-action="restart" data-agent-id="${escapeHtml(agent.id)}">Restart</button>
          <button class="btn btn-secondary" data-action="test" data-agent-id="${escapeHtml(agent.id)}">Send Test</button>
          <button class="btn btn-danger" data-action="delete" data-agent-id="${escapeHtml(agent.id)}">Delete</button>
        </div>
      </section>
    `;
  }

  function personaTabMarkup(agent, detail) {
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
            <input name="mcp_servers" value="${escapeHtml((detail.mcp_servers || ["*"]).join(", "))}" />
          </div>
        </div>
        <div class="field">
          <label>SOUL.md</label>
          <textarea name="soul">${escapeHtml(detail.soul || "")}</textarea>
        </div>
        <div class="form-grid">
          <div class="field">
            <label>Bot User ID</label>
            <input name="bot_user_id" value="${escapeHtml(detail.bot_user_id || "")}" />
          </div>
          <div class="field">
            <label>Enabled</label>
            <select name="enabled">
              <option value="true" ${detail.enabled ? "selected" : ""}>true</option>
              <option value="false" ${detail.enabled ? "" : "selected"}>false</option>
            </select>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-primary" type="submit">Save Persona</button>
          <button class="btn btn-secondary" type="button" data-action="link-bot" data-agent-id="${escapeHtml(agent.id)}">Link Bot ID</button>
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
      <pre class="transcript">${escapeHtml(transcriptCache[agent.id] || "(select a session)")}</pre>
    `;
  }

  function tabPanelMarkup(agent, detail) {
    if (activeTab === "memory") {
      return memoryTabMarkup(agent);
    }
    if (activeTab === "procedures") {
      return proceduresTabMarkup(agent);
    }
    if (activeTab === "sessions") {
      return sessionsTabMarkup(agent);
    }
    return personaTabMarkup(agent, detail);
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

    const activeAgent = getActiveAgent(agents);
    if (activeAgent) {
      try {
        detailCache[activeAgent.id] = await ensureAgentDetail(activeAgent.id);
      } catch (_err) {
        detailCache[activeAgent.id] = detailCache[activeAgent.id] || activeAgent;
      }
    }

    const bridgeLabel = setup.bridge_running ? "running" : "stopped";
    const bridgeClass = setup.bridge_running ? "ok" : "error";
    const detail = activeAgent ? detailCache[activeAgent.id] || activeAgent : null;

    root.innerHTML = `
      <div class="view-stack">
        ${notice?.text ? `<div class="notice ${notice.tone}">${escapeHtml(notice.text)}</div>` : ""}

        <div class="step-panel">
          <h2>Bridge Status</h2>
          <div class="actions">
            <span class="status-pill ${bridgeClass}">${escapeHtml(bridgeLabel)}</span>
            <span class="agent-meta">space: ${escapeHtml(setup.space_id || "(not set)")}</span>
          </div>
          <div class="actions">
            <button class="btn btn-primary" id="bridge-start-btn">Start Bridge</button>
            <button class="btn btn-secondary" id="bridge-stop-btn">Stop Bridge</button>
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
            <div class="field" style="grid-column: 1 / -1;">
              <label>SOUL.md</label>
              <textarea name="soul" placeholder="Persona and tone"></textarea>
            </div>
            <div class="actions" style="grid-column: 1 / -1;">
              <button class="btn btn-primary" type="submit">Create Agent</button>
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
          <h2>Agent Selector</h2>
          ${
            agents.length
              ? `<div class="agent-selector">${selectorMarkup(agents)}</div>`
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
                  ${tabButtonMarkup("memory", activeTab, "Memory")}
                  ${tabButtonMarkup("procedures", activeTab, "Procedures")}
                  ${tabButtonMarkup("sessions", activeTab, "Sessions")}
                </div>
                <div class="tab-panel">${tabPanelMarkup(activeAgent, detail)}</div>
              </div>
            `
            : ""
        }
      </div>
    `;

    root.querySelector("#bridge-start-btn")?.addEventListener("click", async () => {
      try {
        await startBridge();
        setNotice("success", "Bridge started.");
        await onSetupChange();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to start bridge: ${message}`);
        rerender();
      }
    });

    root.querySelector("#bridge-stop-btn")?.addEventListener("click", async () => {
      try {
        await stopBridge();
        setNotice("info", "Bridge stopped.");
        rerender();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to stop bridge: ${message}`);
        rerender();
      }
    });

    root.querySelector("#create-agent-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const name = String(data.get("name") || "").trim();
      if (!name) {
        setNotice("error", "Agent name is required.");
        rerender();
        return;
      }

      try {
        const created = await createAgent({
          name,
          emoji: String(data.get("emoji") || "🤖").trim() || "🤖",
          model: String(data.get("model") || "gemini").trim() || "gemini",
          mcp_servers: parseMcpServers(data.get("mcp_servers")),
          soul: String(data.get("soul") || ""),
        });
        activeAgentId = created.id;
        setNotice("success", `Agent ${name} created.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to create agent: ${message}`);
      }
      rerender();
    });

    for (const tabButton of root.querySelectorAll("button[data-tab]")) {
      tabButton.addEventListener("click", () => {
        activeTab = tabButton.dataset.tab || "persona";
        rerender();
      });
    }

    for (const button of root.querySelectorAll("button[data-action]")) {
      button.addEventListener("click", async () => {
        const action = button.dataset.action;
        const agentId = button.dataset.agentId;

        if (action === "select-agent") {
          activeAgentId = agentId || null;
          activeTab = "persona";
          rerender();
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
          } else if (!action || !agentId) {
            return;
          } else if (action === "start") {
            await startAgent(agentId);
            setNotice("success", `Started ${agentId}.`);
          } else if (action === "stop") {
            await stopAgent(agentId);
            setNotice("info", `Stopped ${agentId}.`);
          } else if (action === "restart") {
            await restartAgent(agentId);
            setNotice("success", `Restarted ${agentId}.`);
          } else if (action === "delete") {
            await deleteAgent(agentId);
            delete detailCache[agentId];
            delete memoryCache[agentId];
            delete proceduresCache[agentId];
            delete sessionsCache[agentId];
            delete transcriptCache[agentId];
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
              transcriptCache[agentId] = JSON.stringify(payload, null, 2);
              setNotice("info", `Loaded session ${sessionId}.`);
            }
          } else if (action === "link-bot") {
            const input = formFieldForAgent(root, agentId, "bot_user_id");
            const botUserId = input?.value?.trim();
            if (!botUserId) {
              setNotice("error", "Bot user id is empty.");
            } else {
              await linkAgentBot(agentId, botUserId);
              setNotice("success", `Linked bot id for ${agentId}.`);
            }
          }
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setNotice("error", `${action || "action"} failed${agentId ? ` for ${agentId}` : ""}: ${message}`);
        }

        rerender();
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
          const payload = {
            name: String(data.get("name") || "").trim(),
            emoji: String(data.get("emoji") || "🤖").trim() || "🤖",
            model: String(data.get("model") || "gemini").trim() || "gemini",
            soul: String(data.get("soul") || ""),
            mcp_servers: parseMcpServers(data.get("mcp_servers")),
            enabled: String(data.get("enabled") || "true") === "true",
            bot_user_id: String(data.get("bot_user_id") || "").trim() || null,
          };
          detailCache[agentId] = await updateAgent(agentId, payload);
          setNotice("success", `Updated ${agentId}.`);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setNotice("error", `Failed to update ${agentId}: ${message}`);
        }
        rerender();
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

  await rerender();

  return {
    destroy() {
      disposed = true;
    },
  };
}
