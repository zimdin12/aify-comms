// Codex MCP env-var writer, moved from install.sh without changing the TOML rewrite.
const fs = require("fs");
const file = process.argv[2];
// Names of env vars the wrapper exports that the inner aify-comms MCP
// server child needs to register correctly. Kept in sync with the
// codex-aify wrapper exports (install.sh:186-237) + the bridge spawn
// env in mcp/stdio/terminal-env.js (AIFY_MANAGED_VIA_WRAPPER, etc.).
// PATH/HOME are forwarded by codex by default (DEFAULT_ENV_VARS in
// codex-rs/rmcp-client/src/utils.rs), so we do not list them here.
const desired = [
  "AIFY_AGENT_ID",
  "AIFY_AGENT_ROLE",
  "AIFY_AGENT_CWD",
  "AIFY_SESSION_MODE",
  "AIFY_SESSION_HANDLE",
  "AIFY_RUNTIME",
  "AIFY_TERMINAL_ID",
  "AIFY_MANAGED_VIA_WRAPPER",
  "AIFY_COMMS_AGENT_ID",
  "AIFY_COMMS_URL",
  "AIFY_ENV_URL",
  "AIFY_ENV_INSTANCE",
  "AIFY_LIFETIME",
  "AIFY_API_KEY",
  "CODEX_THREAD_ID",
  "AIFY_CODEX_APP_SERVER_URL",
];
let text = "";
try { text = fs.readFileSync(file, "utf8"); } catch (_) { process.exit(0); }
const lines = text.split(/\r?\n/);
const headerRe = /^\[mcp_servers\.aify-comms\]\s*$/;
let headerIdx = -1;
for (let i = 0; i < lines.length; i++) {
  if (headerRe.test(lines[i])) { headerIdx = i; break; }
}
if (headerIdx < 0) process.exit(0);
// section end = next "[..." section OR EOF
let endIdx = lines.length;
for (let i = headerIdx + 1; i < lines.length; i++) {
  if (/^\[/.test(lines[i])) { endIdx = i; break; }
}
// Remove any existing env_vars line (handles multi-line inline arrays too)
for (let i = headerIdx + 1; i < endIdx; i++) {
  if (/^\s*env_vars\s*=/.test(lines[i])) {
    let j = i;
    let bracketBalance = 0;
    for (; j < endIdx; j++) {
      for (const ch of lines[j]) {
        if (ch === "[") bracketBalance++;
        else if (ch === "]") bracketBalance--;
      }
      if (bracketBalance <= 0 && j >= i) break;
    }
    lines.splice(i, j - i + 1);
    endIdx -= (j - i + 1);
    i--;
  }
}
const envVarsLine = "env_vars = [" + desired.map((n) => JSON.stringify(n)).join(", ") + "]";
lines.splice(headerIdx + 1, 0, envVarsLine);
fs.writeFileSync(file, lines.join("\n"));
