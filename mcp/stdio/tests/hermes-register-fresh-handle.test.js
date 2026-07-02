#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const serverText = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");

const registerStart = serverText.indexOf('server.tool(\n  "comms_register"');
assert.ok(registerStart >= 0, "comms_register tool should exist");
const registerBody = serverText.slice(registerStart, serverText.indexOf("const capabilities =", registerStart));

assert.ok(
  registerBody.includes("const hermesGatewayRegistration =") &&
    registerBody.includes('resolvedRuntime === "hermes"') &&
    registerBody.includes("runtimeConfig?.gatewayUrl"),
  "Hermes live-gateway registration should be detected before choosing a session handle",
);
assert.match(
  registerBody,
  /const allowPreviousSessionHandle =\s*!\(hermesGatewayRegistration && !String\(sessionHandle \|\| ""\)\.trim\(\)\)/s,
  "Fresh Hermes live-gateway registration without an explicit handle must not reuse previousInfo.sessionHandle",
);
assert.match(
  registerBody,
  /\(allowPreviousSessionHandle \? previousInfo\?\.sessionHandle : ""\)/,
  "Previous handle reuse should be gated by allowPreviousSessionHandle",
);

// 46f313b (2026-06-03, agent-keyed gateway marker): the first-choice source is
// now the module-level AIFY_HERMES_GATEWAY_URL — the startup-validated env URL,
// with a fallback to the AGENT-keyed gateway marker when the env is the literal
// "${AIFY_HERMES_GATEWAY_URL}" placeholder. It still precedes the cwd runtime
// marker, preserving this test's contract: the current MCP process's own gateway
// wins over cwd-keyed markers (which collide for same-folder agents).
assert.match(
  serverText,
  /const rawGatewayUrl = String\(AIFY_HERMES_GATEWAY_URL \|\| process\.env\.AIFY_HERMES_GATEWAY_URL \|\| marker\?\.gatewayUrl \|\| ""\)\.trim\(\)/,
  "Hermes registration must prefer the current MCP process gateway over cwd runtime markers",
);

assert.match(
  serverText,
  /terminalId: cleanEnvPlaceholder\(process\.env\.AIFY_TERMINAL_ID \|\| ""\)/,
  "Auto-registration must not persist unresolved ${AIFY_TERMINAL_ID} placeholders",
);

assert.match(
  serverText,
  /terminalId: cleanEnvPlaceholder\(process\.env\.AIFY_TERMINAL_ID \|\| info\.terminalId \|\| ""\)/,
  "Auto re-registration must sanitize terminal id placeholders",
);

console.log("hermes-register-fresh-handle.test.js: all assertions passed");
