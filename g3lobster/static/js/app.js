import { getSetupStatus } from "./api.js";
import { render as renderWizard } from "./wizard.js";
import { render as renderAgents } from "./agents.js";

const root = document.getElementById("app-root");

let activeView = null;

function destroyActiveView() {
  if (activeView && typeof activeView.destroy === "function") {
    activeView.destroy();
  }
  activeView = null;
}

async function renderApp() {
  destroyActiveView();
  root.innerHTML = "<p class='empty'>Loading...</p>";

  try {
    const status = await getSetupStatus();
    root.innerHTML = "";
    if (!status.completed) {
      activeView = await renderWizard(root, { status, onComplete: renderApp });
      return;
    }
    activeView = await renderAgents(root, { status, onSetupChange: renderApp });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    root.innerHTML = `<div class='notice error'>Failed to load app: ${message}</div>`;
  }
}

renderApp();
