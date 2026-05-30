#!/usr/bin/env node
// Guards the Plan 1.4 (2026-05-30) hermes wrapper rewrite: the legacy
// `hermes dashboard --tui` gateway-spawn + token-capture + AIFY_HERMES_GATEWAY_URL
// export is GONE. Managed/resident hermes delivery now flows through the per-agent
// api_server daemon (hermes-daemon-cli.js) + the hermes-channel.js sidecar.
//
// Task 1.4b (review-found defect): the resident path must NOT export
// HERMES_TUI_GATEWAY_URL. That env var attaches the TUI to a tui_gateway
// WebSocket (`ws://…/api/ws`), but in this hermes build /api/ws is served only by
// a separate `hermes dashboard` (uvicorn) process on the single fixed
// HERMES_DASHBOARD_PORT — NOT by the per-agent `hermes gateway run` daemon, which
// binds only platform adapters (the api_server HTTP port). Pointing
// HERMES_TUI_GATEWAY_URL at the api_server HTTP port produced a WS URL that could
// never connect. The resident TUI now resumes the pinned session and spawns its
// own local gateway instead.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const installText = fs.readFileSync(path.join(repoRoot, "install.sh"), "utf8");

// 1. The dead dashboard gateway-spawn is removed from BOTH wrappers.
assert.doesNotMatch(
  installText,
  /dashboard --tui --port/,
  "hermes-aify must no longer spawn `hermes dashboard --tui --port` (legacy gateway-spawn removed)",
);
assert.doesNotMatch(
  installText,
  /Start-AifyHermesDashboard/,
  "PowerShell hermes-aify must no longer define/use Start-AifyHermesDashboard",
);

// 2. AIFY_HERMES_GATEWAY_URL is no longer EXPORTED by the wrapper. Neither is
//    HERMES_TUI_GATEWAY_URL (see assertion 4) — the wrapper sets NO gateway URL.
assert.doesNotMatch(
  installText,
  /export AIFY_HERMES_GATEWAY_URL=/,
  "bash hermes-aify must not export AIFY_HERMES_GATEWAY_URL anymore",
);
assert.doesNotMatch(
  installText,
  /\$env:AIFY_HERMES_GATEWAY_URL =/,
  "PowerShell hermes-aify must not set $env:AIFY_HERMES_GATEWAY_URL anymore",
);

// 3. The per-agent daemon + sidecar model is wired in BOTH wrappers.
assert.match(
  installText,
  /hermes-daemon-cli\.js/,
  "hermes-aify must invoke the per-agent daemon CLI (hermes-daemon-cli.js)",
);
assert.match(
  installText,
  /hermes-channel\.js/,
  "hermes-aify must exec the channel sidecar (hermes-channel.js) on managed launch",
);
assert.match(
  installText,
  /AIFY_CHANNELS_ENABLED/,
  "managed hermes launch must set AIFY_CHANNELS_ENABLED",
);

// 4. The resident-attach path must NOT set HERMES_TUI_GATEWAY_URL (Task 1.4b):
//    the per-agent `hermes gateway run` daemon serves no /api/ws WebSocket, so a
//    ws:// URL there could never connect. The path documents WHY (the asymmetry)
//    and resumes the pinned session instead.
assert.doesNotMatch(
  installText,
  /HERMES_TUI_GATEWAY_URL\s*=/,
  "resident path must NOT set HERMES_TUI_GATEWAY_URL — the per-agent daemon has no WS gateway port",
);
assert.match(
  installText,
  /we deliberately do NOT (export|set) HERMES_TUI_GATEWAY_URL/,
  "resident path must document why HERMES_TUI_GATEWAY_URL is not set",
);
// And it still resumes the agent's pinned session so the operator drives the
// SAME session the sidecar uses.
assert.match(
  installText,
  /--resume.*PINNED_SESSION|--resume', \$pinnedSession/,
  "resident path must --resume the agent's pinned session",
);

console.log("hermes-gateway-env.test.js: all assertions passed");
