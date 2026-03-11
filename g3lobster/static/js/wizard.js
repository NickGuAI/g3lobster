import {
  completeAuth,
  configureSpace,
  createAgent,
  getSetupStatus,
  listAgents,
  startAgent,
  startBridge,
  testAgent,
  testAuth,
  toggleDebugMode,
  uploadCredentials,
} from "./api.js";

function escapeHtml(str) {
  const div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

const STEP_LABELS = [
  "Credentials",
  "Space",
  "First Agent",
  "Launch",
];

function initialStepFromStatus(status) {
  if (!status.credentials_ok || !status.auth_ok) {
    return 1;
  }
  if (!status.space_configured) {
    return 2;
  }
  if (!status.agents_ready) {
    return 3;
  }
  return 4;
}

function stepDone(step, status) {
  if (step === 1) {
    return status.credentials_ok && status.auth_ok;
  }
  if (step === 2) {
    return status.space_configured;
  }
  if (step === 3) {
    return status.agents_ready;
  }
  return status.bridge_running;
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

function bridgeReadinessDetails(bridge) {
  if (!bridge || !bridge.space_id) {
    return { label: "not configured", className: "warn" };
  }
  if (!bridge.bridge_enabled) {
    return { label: "disabled", className: "stopped" };
  }
  if (bridge.is_running) {
    return { label: "running", className: "ok" };
  }
  return { label: "stopped", className: "error" };
}

export async function render(root, { status, onComplete }) {
  let disposed = false;
  let currentStep = initialStepFromStatus(status);
  let lastStatus = status;
  let authUrl = "";
  let notice = { tone: "info", text: "Follow each step to bring Google Chat + named agents online." };

  async function refreshStatus() {
    lastStatus = await getSetupStatus();
  }

  function setNotice(tone, text) {
    notice = { tone, text };
  }

  function stepperMarkup() {
    return STEP_LABELS.map((label, index) => {
      const step = index + 1;
      const classes = ["step-chip"];
      if (step === currentStep) {
        classes.push("active");
      } else if (stepDone(step, lastStatus)) {
        classes.push("done");
      }
      return `<div class="${classes.join(" ")}"><strong>${step}.</strong> ${label}</div>`;
    }).join("");
  }

  async function renderBody(agents) {
    if (currentStep === 1) {
      return `
        <div class="step-panel">
          <h2>Google Chat Credentials</h2>
          <p class="agent-meta">Upload OAuth client credentials and complete auth.</p>
          <details class="step-help">
            <summary>How to get credentials.json</summary>
            <ol>
              <li>Go to <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">GCP Console &rarr; APIs &amp; Credentials</a></li>
              <li>Click <strong>Create Credentials &rarr; OAuth client ID</strong></li>
              <li>Set application type to <strong>Desktop application</strong> (not Web &mdash; this avoids redirect_uri errors)</li>
              <li>Download the JSON and upload it here</li>
            </ol>
            <p>Make sure the <a href="https://console.cloud.google.com/apis/api/chat.googleapis.com" target="_blank" rel="noreferrer">Google Chat API</a> is enabled on the project.</p>
          </details>
          <div class="kv-row">
            <span>Credentials: <strong>${lastStatus.credentials_ok ? "ready" : "missing"}</strong></span>
            <span>Auth token: <strong>${lastStatus.auth_ok ? "ready" : "missing"}</strong></span>
          </div>
          <div class="field">
            <label for="credentials-file">credentials.json</label>
            <input id="credentials-file" type="file" accept="application/json" />
          </div>
          <div class="actions">
            <button class="btn btn-primary" id="upload-credentials-btn">Upload Credentials</button>
            <button class="btn btn-secondary" id="test-auth-btn">Authenticate</button>
          </div>
          ${authUrl ? `<p class="linkline">Authorize in browser: <a href="${escapeHtml(authUrl)}" target="_blank" rel="noreferrer">${escapeHtml(authUrl)}</a></p>` : ""}
          <details class="step-help">
            <summary>How to complete OAuth</summary>
            <ol>
              <li>Click <strong>Test Auth</strong> above to generate an authorization URL</li>
              <li>Open the URL in your browser and grant consent</li>
              <li>Google will redirect to <code>http://localhost</code> &mdash; copy the full URL from the browser bar</li>
              <li>Paste the URL below and click <strong>Complete Auth</strong></li>
            </ol>
          </details>
          <div class="form-grid">
            <div class="field">
              <label for="oauth-code">OAuth Code</label>
              <input id="oauth-code" type="text" placeholder="Paste the redirect URL or auth code" />
            </div>
          </div>
          <div class="actions">
            <button class="btn btn-secondary" id="complete-auth-btn">Complete Auth</button>
          </div>
        </div>
      `;
    }

    if (currentStep === 2) {
      return `
        <div class="step-panel">
          <h2>Space Configuration</h2>
          <p class="agent-meta">Set the Google Chat space where agents will operate.</p>
          <details class="step-help">
            <summary>How to find the Space ID</summary>
            <ol>
              <li>Open <a href="https://chat.google.com" target="_blank" rel="noreferrer">Google Chat</a></li>
              <li>Navigate to the space you want agents to use</li>
              <li>Copy the URL from the browser &mdash; it looks like <code>https://mail.google.com/chat/u/0/#chat/space/AAAA...</code></li>
              <li>The Space ID is <code>spaces/AAAA...</code> (the part after <code>/space/</code>, prefixed with <code>spaces/</code>)</li>
            </ol>
            <p>Alternatively, click the space name &rarr; <strong>Space details</strong> to see the space info.</p>
          </details>
          <div class="form-grid">
            <div class="field">
              <label for="space-id">Space ID</label>
              <input id="space-id" type="text" placeholder="spaces/AAAA..." value="${escapeHtml(lastStatus.space_id || "")}" />
            </div>
            <div class="field">
              <label for="space-name">Space Name</label>
              <input id="space-name" type="text" placeholder="Ops Room" value="${escapeHtml(lastStatus.space_name || "")}" />
            </div>
          </div>
          <div class="actions">
            <button class="btn btn-primary" id="save-space-btn">Save Space</button>
          </div>
        </div>
      `;
    }

    if (currentStep === 3) {
      return `
        <div class="step-panel">
          <h2>Create First Agent</h2>
          <p class="agent-meta">Define identity, persona, and model options for your first named bot.</p>
          <div class="form-grid">
            <div class="field">
              <label for="agent-name">Name</label>
              <input id="agent-name" type="text" placeholder="Luna" />
            </div>
            <div class="field">
              <label for="agent-emoji">Emoji</label>
              <input id="agent-emoji" type="text" value="🤖" />
            </div>
            <div class="field">
              <label for="agent-model">Model</label>
              <input id="agent-model" type="text" value="gemini" />
            </div>
            <div class="field">
              <label for="agent-mcp">MCP Servers</label>
              <input id="agent-mcp" type="text" value="*" />
            </div>
          </div>
          <div class="field">
            <label for="agent-soul">SOUL.md Persona</label>
            <textarea id="agent-soul" placeholder="Tone, role, guardrails"></textarea>
          </div>
          <div class="actions">
            <button class="btn btn-primary" id="create-agent-btn">Create Agent</button>
          </div>
        </div>
      `;
    }

    const bridgeByAgent = new Map((lastStatus.agent_bridges || []).map((item) => [item.agent_id, item]));
    const bridgeRows = agents.length
      ? agents.map((agent) => {
          const bridge = bridgeByAgent.get(agent.id) || {
            agent_id: agent.id,
            space_id: agent.space_id || null,
            bridge_enabled: agent.bridge_enabled || false,
            is_running: agent.bridge_running || false,
          };
          const details = bridgeReadinessDetails(bridge);
          return `
            <tr>
              <td>${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</td>
              <td><code>${escapeHtml(bridge.space_id || "(not set)")}</code></td>
              <td><span class="status-pill ${escapeHtml(bridge.bridge_enabled ? "ok" : "stopped")}">${escapeHtml(bridge.bridge_enabled ? "enabled" : "disabled")}</span></td>
              <td><span class="status-pill ${escapeHtml(details.className)}">${escapeHtml(details.label)}</span></td>
            </tr>
          `;
        }).join("")
      : "<tr><td colspan='4' class='empty'>No agents configured.</td></tr>";
    const bridgeRequiredCount = agents.filter((agent) => {
      const bridge = bridgeByAgent.get(agent.id) || {};
      return Boolean(bridge.bridge_enabled) && Boolean(bridge.space_id);
    }).length;
    const bridgeRunningCount = agents.filter((agent) => {
      const bridge = bridgeByAgent.get(agent.id) || {};
      return Boolean(bridge.bridge_enabled) && Boolean(bridge.space_id) && Boolean(bridge.is_running);
    }).length;

    const checklist = [
      ["Credentials uploaded", lastStatus.credentials_ok],
      ["OAuth complete", lastStatus.auth_ok],
      ["Space configured", lastStatus.space_configured],
      ["At least one agent", lastStatus.agents_ready],
      ["Bridges running", lastStatus.bridge_running],
    ]
      .map(([label, ok]) => `<li>${ok ? "✅" : "⬜"} ${label}</li>`)
      .join("");

    const emailStatus = lastStatus.email_enabled
      ? `enabled — ${escapeHtml(lastStatus.email_base_address || "(no address)")} (poll: ${lastStatus.email_poll_interval_s}s)`
      : "disabled";

    const debugLabel = lastStatus.debug_mode ? "ON" : "OFF";
    const debugClass = lastStatus.debug_mode ? "ok" : "";

    return `
      <div class="step-panel">
        <h2>Launch</h2>
        <p class="agent-meta">Start enabled agents and begin polling Google Chat.</p>
        <ul>${checklist}</ul>
        <div class="agent-meta">${escapeHtml(String(bridgeRunningCount))}/${escapeHtml(String(bridgeRequiredCount))} required agent bridges running</div>
        <table class="bridge-table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Space</th>
              <th>Bridge</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>${bridgeRows}</tbody>
        </table>
        <div class="actions">
          <button class="btn btn-primary" id="launch-btn">Launch Bridge + Agents</button>
        </div>
      </div>
      <div class="step-panel">
        <h2>Email Bridge</h2>
        <p class="agent-meta">Email bridge status: <strong>${emailStatus}</strong></p>
        <p class="agent-meta">Configure email settings in <code>config.yaml</code> under the <code>email</code> section.</p>
      </div>
      <div class="step-panel">
        <h2>Debug Mode</h2>
        <p class="agent-meta">When enabled, error details are sent to the Google Chat thread instead of generic messages.</p>
        <div class="actions">
          <span class="status-pill ${debugClass}">${escapeHtml(debugLabel)}</span>
          <button class="btn btn-secondary" id="toggle-debug-btn">Toggle Debug Mode</button>
        </div>
        <p class="agent-meta">Override via env: <code>G3LOBSTER_DEBUG_MODE=true</code></p>
      </div>
    `;
  }

  async function rerender() {
    if (disposed) {
      return;
    }

    let agents = [];
    try {
      agents = await listAgents();
    } catch (_err) {
      agents = [];
    }

    root.innerHTML = `
      <div class="view-stack">
        <div class="stepper">${stepperMarkup()}</div>
        ${notice?.text ? `<div class="notice ${notice.tone}">${notice.text}</div>` : ""}
        ${await renderBody(agents)}
        <div class="step-nav">
          <button class="btn btn-secondary" id="wizard-prev" ${currentStep <= 1 ? "disabled" : ""}>Back</button>
          <button class="btn btn-secondary" id="wizard-next" ${currentStep >= 4 ? "disabled" : ""}>Next</button>
        </div>
      </div>
    `;

    root.querySelector("#wizard-prev")?.addEventListener("click", () => {
      currentStep = Math.max(1, currentStep - 1);
      rerender();
    });

    root.querySelector("#wizard-next")?.addEventListener("click", () => {
      currentStep = Math.min(4, currentStep + 1);
      rerender();
    });

    root.querySelector("#upload-credentials-btn")?.addEventListener("click", async () => {
      const input = root.querySelector("#credentials-file");
      const file = input?.files?.[0];
      if (!file) {
        setNotice("error", "Select a credentials.json file first.");
        rerender();
        return;
      }

      try {
        const text = await file.text();
        const payload = JSON.parse(text);
        await uploadCredentials(payload);
        await refreshStatus();
        setNotice("success", "Credentials uploaded.");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to upload credentials: ${message}`);
      }
      rerender();
    });

    root.querySelector("#test-auth-btn")?.addEventListener("click", async () => {
      try {
        const result = await testAuth(true);
        authUrl = result.auth_url || "";
        setNotice("info", "Open the authorization URL, then paste the returned code.");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Auth failed: ${message}`);
      }
      rerender();
    });

    root.querySelector("#complete-auth-btn")?.addEventListener("click", async () => {
      const codeInput = root.querySelector("#oauth-code");
      const code = codeInput?.value?.trim();
      if (!code) {
        setNotice("error", "Enter OAuth code first.");
        rerender();
        return;
      }

      try {
        await completeAuth(code);
        await refreshStatus();
        setNotice("success", "OAuth complete.");
        if (stepDone(1, lastStatus)) {
          currentStep = Math.max(currentStep, 2);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `OAuth completion failed: ${message}`);
      }
      rerender();
    });

    root.querySelector("#save-space-btn")?.addEventListener("click", async () => {
      const spaceId = root.querySelector("#space-id")?.value?.trim();
      const spaceName = root.querySelector("#space-name")?.value?.trim() || null;
      if (!spaceId) {
        setNotice("error", "Space ID is required.");
        rerender();
        return;
      }

      try {
        await configureSpace({ space_id: spaceId, space_name: spaceName });
        await refreshStatus();
        setNotice("success", "Space saved.");
        if (stepDone(2, lastStatus)) {
          currentStep = Math.max(currentStep, 3);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to save space: ${message}`);
      }
      rerender();
    });

    root.querySelector("#create-agent-btn")?.addEventListener("click", async () => {
      const name = root.querySelector("#agent-name")?.value?.trim();
      if (!name) {
        setNotice("error", "Agent name is required.");
        rerender();
        return;
      }

      const emoji = root.querySelector("#agent-emoji")?.value?.trim() || "🤖";
      const model = root.querySelector("#agent-model")?.value?.trim() || "gemini";
      const soul = root.querySelector("#agent-soul")?.value || "";
      const mcp = parseMcpServers(root.querySelector("#agent-mcp")?.value);

      try {
        await createAgent({ name, emoji, soul, model, mcp_servers: mcp });
        await refreshStatus();
        setNotice("success", "Agent created.");
        if (stepDone(3, lastStatus)) {
          currentStep = Math.max(currentStep, 4);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to create agent: ${message}`);
      }
      rerender();
    });

    root.querySelector("#toggle-debug-btn")?.addEventListener("click", async () => {
      try {
        const result = await toggleDebugMode();
        lastStatus.debug_mode = result.debug_mode;
        setNotice("success", `Debug mode ${result.debug_mode ? "enabled" : "disabled"}.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to toggle debug mode: ${message}`);
      }
      rerender();
    });

    root.querySelector("#launch-btn")?.addEventListener("click", async () => {
      try {
        const currentAgents = await listAgents();
        await Promise.all(
          currentAgents
            .filter((agent) => agent.enabled && agent.state === "stopped")
            .map((agent) => startAgent(agent.id)),
        );
        await startBridge();
        await refreshStatus();
        setNotice("success", "Bridge started.");
        if (lastStatus.completed) {
          await onComplete();
          return;
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Launch failed: ${message}`);
      }
      rerender();
    });
  }

  await rerender();

  return {
    destroy() {
      disposed = true;
    },
  };
}
