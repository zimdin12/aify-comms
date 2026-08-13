#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const serverText = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");

// THE TOOL LIVES IN `registration-tool.mjs` SINCE v0.5.4, and the two remaining source assertions below are
// repointed at it rather than converted. They are about SESSION-HANDLE SELECTION — a different subject from
// the extraction that moved them, and one that needs a previous-info fixture to test behaviourally. That
// conversion is worth doing and is not this slice's business; the gateway-precedence assertion that used to
// sit below them WAS converted, and now lives in `registration-inputs.test.js`.
//
// Located by NAME rather than by indentation. The previous version searched for `server.tool(\n  "…"` — two
// literal spaces — so wrapping the tool in a registration function broke it with no behavioural change.
const toolText = fs.readFileSync(path.join(__dirname, "..", "registration-tool.mjs"), "utf8");
const registerStart = toolText.search(/^\s*server\.tool\(\s*$/m);
assert.ok(registerStart >= 0, "comms_register tool should exist");
assert.ok(toolText.includes('"comms_register"'), "…and registration-tool.mjs should be the module holding it");
const registerBody = toolText.slice(registerStart, toolText.indexOf("const capabilities =", registerStart));

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

// THE GATEWAY-PRECEDENCE ASSERTION THAT WAS HERE HAS MOVED, and became a real test on the way.
//
// It was a regex over `server.js` matching the exact text of the `rawGatewayUrl` chain — added by 46f313b
// (2026-06-03, agent-keyed gateway marker) to pin that this MCP process's own gateway outranks the cwd
// runtime marker, because cwd-keyed markers collide for same-folder agents. It proved the line was WRITTEN;
// it could not prove the precedence held, and it broke the moment `resolvedRuntimeConfigForRegistration`
// moved to its owner in v0.5.4 without a single behavioural change.
//
// `tests/registration-inputs.test.js` now asserts the same contract by resolving a real config against a
// real marker written by `writeRuntimeMarker` under an isolated `XDG_STATE_HOME`: the marker supplies the
// gateway when nothing else does, and is outranked when this process has its own. A reordering mutation of
// that chain fails it, which the regex form could not distinguish from a reformatting.

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
