#!/usr/bin/env node
// The aify-comms entry inside a hermes `config.yaml`, built as a value and written once.
//
// WHY THIS IS A FILE AND NOT A HEREDOC. It was ~90 lines of JavaScript embedded in a bash
// single-quoted string inside `install.sh`, which made it unreachable from any test: the hermes
// install tests that exist say so in their own docstring -- "install.sh static-text smoke checks
// (no bash invocation)" -- and prove only that a line was WRITTEN, never that the emitted config
// contains it. That is the same shape `claude-wrapper-determinism.test.js` was created to end.
//
// WHAT WAS WRONG WHILE IT WAS UNREACHABLE. The entry carried the service URL and NOT the API key.
// Measured 2026-08-30 with controls: `AIFY_API_KEY` appeared 0 times in the emitting function
// against 3 for `AIFY_SERVER_URL`, and 0 for a string known to be absent. So the moment an
// operator set `API_KEY`, hermes -- the runtime the fleet actually runs on -- would 401 on every
// call, with no error naming the cause. `install_opencode_config` and `install_pi_config` both
// pass the key, and those two installs are DISABLED; the enabled one did not.
//
// WHY THE KEY IS NOT GIVEN THE `${VAR}` FALLBACK THE URLS GET. Hermes filters env down to
// `_SAFE_ENV_KEYS` before spawning a stdio MCP child, and resolves `${VAR}` at spawn time from
// its OWN env, which it inherits from the hermes-aify wrapper. The wrapper exports the URLs; it
// does not export the key (0 occurrences across all four launcher templates, against a control of
// 2/8/5 for `SERVER_URL`). An interpolation that can only ever resolve to empty would write an
// empty credential and turn a missing key into a wrong one. So the key is emitted as a literal
// when there is one, and omitted entirely when there is not -- the same `if (apiKey)` shape the
// opencode and pi installers already use.

import fs from "node:fs";
import { pathToFileURL } from "node:url";

/**
 * Env names forwarded verbatim to the MCP child, each as a `${NAME}` hermes resolves at spawn.
 *
 * Hermes passes only `_SAFE_ENV_KEYS` (PATH, HOME and friends) to a stdio MCP child, so anything
 * the bridge needs has to be named here explicitly. `AIFY_AGENT_ID`, `AIFY_SESSION_MODE` and
 * `AIFY_MANAGED_VIA_WRAPPER` are load-bearing rather than informational: without them the inner
 * bridge never registers in `bridge_instances` and dispatch sits queued for ever -- observed
 * 2026-05-26 with the wrapper PTY attached, the hermes TUI rendered and the MCP server loaded,
 * but no `/agents` POST. `AIFY_COMMS_AGENT_ID` and `AIFY_TERMINAL_ID` are kept for symmetry with
 * `terminalChildEnv`.
 */
export const FORWARDED_ENV_NAMES = [
  "AIFY_AGENT_ID",
  "AIFY_COMMS_AGENT_ID",
  "AIFY_AGENT_ROLE",
  "AIFY_AGENT_CWD",
  "AIFY_SESSION_MODE",
  "AIFY_SESSION_HANDLE",
  "AIFY_EXPLICIT_SESSION_HANDLE",
  "AIFY_RUNTIME",
  "AIFY_TERMINAL_ID",
  "AIFY_MANAGED_VIA_WRAPPER",
  "HERMES_SESSION_ID",
  "AIFY_HERMES_GATEWAY_URL",
  "AIFY_HERMES_GATEWAY_TOKEN",
  "HERMES_TUI_GATEWAY_URL",
];

/** The env names that carry the service API key. Both are read by the bridge; both are set. */
export const API_KEY_ENV_NAMES = ["AIFY_API_KEY", "CLAUDE_MCP_API_KEY"];

/**
 * The `aify-comms:` block, as YAML lines at hermes' two-space `mcp_servers` indent.
 *
 * @param serverPath  absolute path to the bridge's `server.js`, as the hermes RUNTIME must read it
 * @param serverUrl   the service endpoint; empty means fall back to `${AIFY_SERVER_URL}`
 * @param apiKey      the service key; empty means emit no key lines at all
 */
export function aifyEntryLines({ serverPath, serverUrl = "", apiKey = "" } = {}) {
  const url = (name) => (serverUrl ? JSON.stringify(serverUrl) : `"\${${name}}"`);
  return [
    "  aify-comms:",
    '    command: "node"',
    "    args:",
    `      - ${JSON.stringify(serverPath)}`,
    "    env:",
    ...FORWARDED_ENV_NAMES.map((name) => `      ${name}: "\${${name}}"`),
    // HTTP mode is reached ONLY when one of these is set (server.js:94); otherwise the child
    // silently falls back to the local `.messages/` FILE store and replies never reach the
    // service. A literal is preferred when we have one because it depends on no env at all.
    `      AIFY_SERVER_URL: ${url("AIFY_SERVER_URL")}`,
    `      CLAUDE_MCP_SERVER_URL: ${url("CLAUDE_MCP_SERVER_URL")}`,
    ...(apiKey ? API_KEY_ENV_NAMES.map((name) => `      ${name}: ${JSON.stringify(apiKey)}`) : []),
  ];
}

/**
 * Return `text` with the aify-comms MCP entry present exactly once.
 *
 * PURE: no filesystem, no process state. Three cases, in the order the original established --
 * replace an existing `aify-comms:` block in place (so re-running the installer REFRESHES the env
 * block; a skip-if-exists guard here once left operators on an entry that predated the env
 * expansion, which broke managed delivery), otherwise insert under an existing `mcp_servers:`,
 * otherwise append a whole `mcp_servers:` section.
 */
export function configWithAifyEntry(text, options) {
  const entry = aifyEntryLines(options);
  const lines = String(text || "").replace(/\s*$/, "").split(/\r?\n/);
  const mcpIndex = lines.findIndex((line) => /^[ \t]*mcp_servers:[ \t]*$/.test(line));

  let existingStart = -1;
  let existingEnd = -1;
  for (let i = 0; i < lines.length; i++) {
    if (/^[ \t]+aify-comms:[ \t]*$/.test(lines[i])) {
      existingStart = i;
      const baseIndent = (lines[i].match(/^[ \t]+/) || [""])[0].length;
      existingEnd = lines.length;
      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].trim() === "") continue;
        const indent = (lines[j].match(/^[ \t]*/) || [""])[0].length;
        if (indent <= baseIndent) { existingEnd = j; break; }
      }
      break;
    }
  }

  if (existingStart >= 0) {
    lines.splice(existingStart, existingEnd - existingStart, ...entry);
    return lines.join("\n") + "\n";
  }
  if (mcpIndex >= 0) {
    lines.splice(mcpIndex + 1, 0, ...entry);
    return lines.join("\n") + "\n";
  }
  const head = lines.filter(Boolean).join("\n");
  return `${head}${lines.some(Boolean) ? "\n\n" : ""}mcp_servers:\n${entry.join("\n")}\n`;
}

/** Read, patch, write. The only side effect in this file. */
export function patchHermesConfigFile(file, options) {
  let text = "";
  try { text = fs.readFileSync(file, "utf8"); } catch { /* absent is an empty config */ }
  fs.writeFileSync(file, configWithAifyEntry(text, options));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [file, serverPath, serverUrl = "", apiKey = ""] = process.argv.slice(2);
  patchHermesConfigFile(file, { serverPath, serverUrl, apiKey });
}
