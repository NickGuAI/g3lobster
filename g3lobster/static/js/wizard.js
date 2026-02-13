import {
  completeAuth,
  configureSpace,
  createAgent,
  getSetupStatus,
  linkAgentBot,
  listAgents,
  listSpaceBots,
  startAgent,
  startBridge,
  testAgent,
  testAuth,
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
  "Register Bot",
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
  if (!status.bridge_running) {
    return 5;
  }
  return 5;
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
  if (step === 4) {
    return false;
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

export async function render(root, { status, onComplete }) {
  let disposed = false;
  let currentStep = initialStepFromStatus(status);
  let lastStatus = status;
  let authUrl = "";
  let detectedBots = null;
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
              <li>Google will redirect to <code>http://localhost</code> &mdash; copy the <code>code</code> parameter from the URL bar</li>
              <li>Paste it below and click <strong>Complete Auth</strong></li>
            </ol>
          </details>
          <div class="form-grid">
            <div class="field">
              <label for="oauth-code">OAuth Code</label>
              <input id="oauth-code" type="text" placeholder="Paste code from consent screen" />
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

    if (currentStep === 4) {
      const agentOptions = agents
        .map((agent) => `<option value="${escapeHtml(agent.id)}">${escapeHtml(agent.emoji)} ${escapeHtml(agent.name)}</option>`)
        .join("");
      const detectedBotOptions = (detectedBots || [])
        .map((b) => `<option value="${escapeHtml(b.user_id)}">${escapeHtml(b.display_name)} (${escapeHtml(b.user_id)})</option>`)
        .join("");
      const hasBots = detectedBots && detectedBots.length > 0;
      return `
        <div class="step-panel">
          <h2>Register Chat Bot</h2>
          <p class="agent-meta">Create a Chat App in GCP, add it to your space, then detect it here.</p>
          <details class="step-help">
            <summary>How to create a Chat App in GCP</summary>
            <ol>
              <li>Go to <a href="https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat" target="_blank" rel="noreferrer">GCP Console &rarr; Chat API &rarr; Configuration</a></li>
              <li>Fill in <strong>App name</strong> (e.g. your agent's name), avatar, and description</li>
              <li>Under <strong>Functionality</strong>, enable "Receive 1:1 messages" and/or "Join spaces and group conversations"</li>
              <li>Under <strong>Connection settings</strong>, choose <strong>Apps Script</strong> or <strong>HTTP endpoint</strong> (the bridge uses polling, so connection type is flexible)</li>
              <li>Under <strong>Visibility</strong>, make the app available to your domain or specific users</li>
              <li>Save, then add the bot to your space: in Google Chat, click <strong>+</strong> next to the space &rarr; <strong>Apps</strong> &rarr; search for your app name</li>
              <li>New apps can take <strong>5&ndash;15 minutes</strong> to appear in search</li>
            </ol>
          </details>
          <div class="actions">
            <button class="btn btn-primary" id="detect-bots-btn">Detect Bots in Space</button>
          </div>
          ${hasBots ? `<p class="agent-meta">Found <strong>${detectedBots.length}</strong> bot(s) in your space:</p>` : ""}
          ${agents.length ? "" : "<p class='empty'>Create an agent first (step 3).</p>"}
          ${
            agents.length
              ? `
            <div class="form-grid">
              <div class="field">
                <label for="link-agent-id">Agent</label>
                <select id="link-agent-id">${agentOptions}</select>
              </div>
              <div class="field">
                <label for="bot-user-id">Bot User ID</label>
                ${hasBots
                  ? `<select id="bot-user-id">${detectedBotOptions}</select>`
                  : `<input id="bot-user-id" type="text" placeholder="Click Detect Bots above, or paste users/123456789" />`
                }
              </div>
            </div>
            <div class="actions">
              <button class="btn btn-primary" id="link-bot-btn">Link Bot</button>
              <button class="btn btn-secondary" id="test-linked-agent-btn">Send Test Message</button>
            </div>
          `
              : ""
          }
        </div>
      `;
    }

    const checklist = [
      ["Credentials uploaded", lastStatus.credentials_ok],
      ["OAuth complete", lastStatus.auth_ok],
      ["Space configured", lastStatus.space_configured],
      ["At least one agent", lastStatus.agents_ready],
      ["Bridge running", lastStatus.bridge_running],
    ]
      .map(([label, ok]) => `<li>${ok ? "✅" : "⬜"} ${label}</li>`)
      .join("");

    return `
      <div class="step-panel">
        <h2>Launch</h2>
        <p class="agent-meta">Start enabled agents and begin polling Google Chat.</p>
        <ul>${checklist}</ul>
        <div class="actions">
          <button class="btn btn-primary" id="launch-btn">Launch Bridge + Agents</button>
        </div>
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
          <button class="btn btn-secondary" id="wizard-next" ${currentStep >= 5 ? "disabled" : ""}>Next</button>
        </div>
      </div>
    `;

    root.querySelector("#wizard-prev")?.addEventListener("click", () => {
      currentStep = Math.max(1, currentStep - 1);
      rerender();
    });

    root.querySelector("#wizard-next")?.addEventListener("click", () => {
      currentStep = Math.min(5, currentStep + 1);
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

    root.querySelector("#detect-bots-btn")?.addEventListener("click", async () => {
      try {
        const result = await listSpaceBots();
        detectedBots = result.bots || [];
        if (detectedBots.length === 0) {
          setNotice("info", "No bots found in the space yet. Make sure you added the Chat App to your space (can take 5-15 min for new apps).");
        } else {
          setNotice("success", `Found ${detectedBots.length} bot(s). Select one and link it to an agent.`);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to detect bots: ${message}`);
      }
      rerender();
    });

    root.querySelector("#link-bot-btn")?.addEventListener("click", async () => {
      const agentId = root.querySelector("#link-agent-id")?.value;
      const botUserId = root.querySelector("#bot-user-id")?.value?.trim();
      if (!agentId || !botUserId) {
        setNotice("error", "Select an agent and provide bot user id.");
        rerender();
        return;
      }

      try {
        await linkAgentBot(agentId, botUserId);
        setNotice("success", "Bot linked to agent.");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to link bot: ${message}`);
      }
      rerender();
    });

    root.querySelector("#test-linked-agent-btn")?.addEventListener("click", async () => {
      const agentId = root.querySelector("#link-agent-id")?.value;
      if (!agentId) {
        setNotice("error", "Select an agent first.");
        rerender();
        return;
      }

      try {
        await testAgent(agentId, "wizard connectivity test");
        setNotice("success", "Test message sent to Google Chat.");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setNotice("error", `Failed to send test message: ${message}`);
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
