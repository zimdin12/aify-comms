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
// call, with no error naming the cause. The opencode and pi config writers
// passed the key, and those two installs were DISABLED; the enabled one did not.
//
// THE KEY IS A LITERAL. Emitted as the `X-API-Key` header when there is one, and omitted entirely when there
// is not: the launchers do not export it, so a `${VAR}` could only ever resolve to empty and turn a missing key
// into a wrong one.
//
// 0.9.11: HTTP ONLY. Hermes takes the comms tools from the service's /mcp/http. The stdio child this entry used
// to start (with its forwarded env and TEMP pins) is gone; an entry left from an older install is replaced.

import fs from "node:fs";
import { pathToFileURL } from "node:url";

/**
 * The `aify-comms:` block, as YAML lines at hermes' two-space `mcp_servers` indent: the service's /mcp/http.
 *
 * Hermes has no per-launch MCP flag, so the agent comes from the gateway's environment: hermes expands
 * `${AIFY_AGENT_ID}` in headers and refuses to connect a remote server while one is unset
 * (tools/mcp_tool_config.py, 0.21.5).
 *
 * @param serverUrl   the service endpoint; empty means fall back to `${AIFY_SERVER_URL}`
 * @param apiKey      the service key; empty means emit no key line at all
 */
export function aifyEntryLines({ serverUrl = "", apiKey = "" } = {}) {
  return [
    "  aify-comms:",
    `    url: ${serverUrl ? JSON.stringify(`${serverUrl.replace(/\/+$/, "")}/mcp/http`) : '"${AIFY_SERVER_URL}/mcp/http"'}`,
    "    headers:",
    '      X-Aify-Agent-Id: "${AIFY_AGENT_ID}"',
    ...(apiKey ? [`      X-API-Key: ${JSON.stringify(apiKey)}`] : []),
  ];
}

/** A top-level YAML key line: `name:` at column 0. */
const isTopLevelKey = (line) => /^[^\s#-][^:]*:/.test(line);

/** Index of the first non-blank line after `start` indented at or above `indent`, else the end. */
function blockEnd(lines, start, indent) {
  for (let j = start + 1; j < lines.length; j++) {
    if (lines[j].trim() === "") continue;
    if ((lines[j].match(/^[ \t]*/) || [""])[0].length <= indent) return j;
  }
  return lines.length;
}

/** The top-level key a line sits under, unquoted, or "" above the first one. */
function sectionOf(lines, index) {
  for (let j = index; j >= 0; j--) if (isTopLevelKey(lines[j])) return lines[j].split(":")[0].trim().replace(/^(["'])(.*)\1$/, "$2");
  return "";
}

// A key line as YAML reads it: plain or quoted, a trailing comment allowed. A quoted `"aify-comms":` the matcher
// missed was left beside the new entry, and a YAML reader kept the old one (review of switch 1ed207d2).
const keyLine = (indent, name) => new RegExp(`^ {${indent}}(["']?)${name}\\1:[ \\t]*(?:#.*)?$`);
const ENTRY_KEY = keyLine(2, "aify-comms");
const SECTION_KEY = keyLine(0, "mcp_servers");
const COMMAND_KEY = /^ {4}(["']?)command\1:/;

/**
 * Return `text` with the aify-comms MCP entry present exactly once, under `mcp_servers:`.
 *
 * PURE: no filesystem, no process state. The entry is REPLACED in place when `mcp_servers:` has one,
 * so re-running the installer refreshes it (a skip-if-exists guard once left operators on an entry that
 * predated the env expansion); otherwise it is inserted under `mcp_servers:`, or a
 * whole section is appended.
 *
 * ONLY INSIDE `mcp_servers:`. This used to replace the first indented `aify-comms:` anywhere, and
 * hermes' `plugins:` has one too: `plugins.entries.aify-comms` (`allow_tool_override`). Each install
 * overwrote that plugin setting with the MCP block one level too shallow, leaving the real entry
 * stale; on 2026-09-28 two such blocks under `plugins:` made the whole config unparseable ("found
 * duplicate key"), and every managed hermes died at start. A two-space `aify-comms:` block carrying
 * `command:` under any other section is that leftover, and is removed.
 */
export function configWithAifyEntry(text, options) {
  const entry = aifyEntryLines(options);
  const lines = String(text || "").replace(/\s*$/, "").split(/\r?\n/);

  for (let i = lines.length - 1; i >= 0; i--) {
    if (!ENTRY_KEY.test(lines[i]) || sectionOf(lines, i) === "mcp_servers") continue;
    const end = blockEnd(lines, i, 2);
    if (lines.slice(i + 1, end).some((line) => COMMAND_KEY.test(line))) lines.splice(i, end - i);
  }

  const mcpIndex = lines.findIndex((line) => SECTION_KEY.test(line));
  if (mcpIndex < 0) {
    const head = lines.filter(Boolean).join("\n");
    return `${head}${lines.some(Boolean) ? "\n\n" : ""}mcp_servers:\n${entry.join("\n")}\n`;
  }
  const sectionEnd = blockEnd(lines, mcpIndex, 0);
  const existing = lines.findIndex((line, i) => i > mcpIndex && i < sectionEnd && ENTRY_KEY.test(line));
  if (existing >= 0) {
    lines.splice(existing, blockEnd(lines, existing, 2) - existing, ...entry);
  } else {
    lines.splice(mcpIndex + 1, 0, ...entry);
  }
  return lines.join("\n") + "\n";
}

/** Read, patch, write. The only side effect in this file. */
export function patchHermesConfigFile(file, options) {
  let text = "";
  try { text = fs.readFileSync(file, "utf8"); } catch { /* absent is an empty config */ }
  fs.writeFileSync(file, configWithAifyEntry(text, options));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [file, serverUrl = "", apiKey = ""] = process.argv.slice(2);
  patchHermesConfigFile(file, { serverUrl, apiKey });
}
