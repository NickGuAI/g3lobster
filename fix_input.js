const fs = require('fs');
const p = 'g3lobster/static/js/agents.js';
let content = fs.readFileSync(p, 'utf8');

const replacement = `
  async function queueRerender() {
    if (disposed) {
      return;
    }

    const ae = document.activeElement;
    if (ae && root.contains(ae) && ["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName)) {
      rerenderQueued = true;
      return;
    }

    if (rerenderInFlight) {
`;

content = content.replace(/async function queueRerender\(\) \{\n\s*if \(disposed\) \{\n\s*return;\n\s*\}\n\n\s*if \(rerenderInFlight\) \{/m, replacement.trim());
fs.writeFileSync(p, content);
