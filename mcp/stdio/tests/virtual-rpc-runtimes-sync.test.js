#!/usr/bin/env node
// Regression test for bridge-side VIRTUAL_RPC_RUNTIMES vs service-side
// VIRTUAL_RPC_COMMANDS_BY_RUNTIME drift. Operator-reported 2026-05-22:
// hermes Console "doesn't spawn pseudo terminal" — root cause was the
// bridge's `findAgentIdForVirtualTerminal` had a hardcoded
// `runtime === "pi"` check, so when hermes/codex/opencode synth
// terminals were added later, the bridge routed their controls
// through the legacy node-pty path, which marked them stopped on
// every Console open. This test prevents that drift class recurring.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..", "..");

// 1. Parse bridge-side VIRTUAL_RPC_RUNTIMES from server.js
const serverPath = path.join(__dirname, "..", "server.js");
const serverText = fs.readFileSync(serverPath, "utf-8");
const bridgeMatch = serverText.match(/^const VIRTUAL_RPC_RUNTIMES\s*=\s*new Set\(\[([^\]]+)\]\)/m);
assert.ok(bridgeMatch, "could not locate VIRTUAL_RPC_RUNTIMES in mcp/stdio/server.js");
const bridgeSet = new Set(
  bridgeMatch[1]
    .split(",")
    .map((s) => s.trim().replace(/^["']|["']$/g, ""))
    .filter(Boolean),
);
assert.ok(bridgeSet.size > 0, "bridge VIRTUAL_RPC_RUNTIMES must be non-empty");

// 2. Parse service-side VIRTUAL_RPC_COMMANDS_BY_RUNTIME from service/control_plane.py
const controlPlanePath = path.join(repoRoot, "service", "control_plane.py");
const apiV2Text = fs.readFileSync(controlPlanePath, "utf-8");
const serviceMatch = apiV2Text.match(/^VIRTUAL_RPC_COMMANDS_BY_RUNTIME\s*=\s*\{([^}]+)\}/m);
assert.ok(serviceMatch, "could not locate VIRTUAL_RPC_COMMANDS_BY_RUNTIME in service/control_plane.py");
const serviceSet = new Set(
  serviceMatch[1]
    .split(",")
    .map((s) => s.split(":")[0].trim().replace(/^["']|["']$/g, ""))
    .filter(Boolean),
);

// 3. Both sets must match exactly
const bridgeArr = [...bridgeSet].sort();
const serviceArr = [...serviceSet].sort();
assert.deepEqual(
  bridgeArr,
  serviceArr,
  `bridge VIRTUAL_RPC_RUNTIMES (${bridgeArr.join(", ")}) does not match service-side VIRTUAL_RPC_COMMANDS_BY_RUNTIME (${serviceArr.join(", ")}). Both must be updated together when adding a runtime that owns a synthesized virtual rpc terminal.`,
);

console.log("virtual-rpc-runtimes-sync.test.js: all assertions passed");
