#!/usr/bin/env node
// Guards the Plan 1.4 (2026-05-30) hermes wrapper rewrite: the legacy
// `hermes dashboard --tui` gateway-spawn + token-capture + AIFY_HERMES_GATEWAY_URL
// export is GONE. Managed/resident hermes delivery now flows through the per-agent
// api_server daemon (hermes-daemon-cli.js) + the hermes-channel.js sidecar, and
// HERMES_TUI_GATEWAY_URL is set ONLY in the resident-TUI attach path.
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

// 2. AIFY_HERMES_GATEWAY_URL is no longer EXPORTED by the wrapper (the only
//    gateway env the resident path sets now is HERMES_TUI_GATEWAY_URL).
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

// 4. The resident-attach path points HERMES_TUI_GATEWAY_URL at the per-agent
//    daemon's WS port (the documented ASYMMETRY).
assert.match(
  installText,
  /ASYMMETRY\(hermes\): resident TUI attaches to the per-agent daemon/,
  "resident path must document the per-agent daemon TUI-attach asymmetry",
);
assert.match(
  installText,
  /HERMES_TUI_GATEWAY_URL="ws:\/\/127\.0\.0\.1:/,
  "resident bash path must set HERMES_TUI_GATEWAY_URL to the per-agent daemon WS",
);

console.log("hermes-gateway-env.test.js: all assertions passed");
