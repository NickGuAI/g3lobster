const fs = require('fs');
const p = 'g3lobster/static/js/agents.js';
let content = fs.readFileSync(p, 'utf8');

// We can add the event listener right near where we define queueRerender, or inside render().
// We'll add it right before `return { destroy() { ... } }` at the end of render().

const listener = `
  root.addEventListener("focusout", (e) => {
    // Wait for the new focus to settle
    setTimeout(() => {
      if (disposed) return;
      const ae = document.activeElement;
      if (!ae || !root.contains(ae) || !["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName)) {
        if (rerenderQueued) {
          queueRerender();
        }
      }
    }, 10);
  });

  return {
`;

content = content.replace(/\n\s*return \{\n\s*destroy\(\) \{/, listener);
fs.writeFileSync(p, content);
