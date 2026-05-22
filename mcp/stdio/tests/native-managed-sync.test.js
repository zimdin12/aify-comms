#!/usr/bin/env node
// Regression test for the bridge-side vs service-side
// NATIVE_MANAGED_RUNTIMES drift that operator hit 2026-05-22.
// When d87457b added hermes to the SERVICE-side set
// (_NATIVE_MANAGED_RUNTIMES in service/routers/api_v2.py) but missed
// the BRIDGE-side set (NATIVE_MANAGED_RUNTIMES in dispatch-execution.js),
// the bridge silently stopped claiming hermes managed dispatches.
// This test prevents that class of drift recurring.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { NATIVE_MANAGED_RUNTIMES } from "../dispatch-execution.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..", "..");

// 1. Bridge-side set exists and is non-empty
assert.ok(NATIVE_MANAGED_RUNTIMES instanceof Set, "bridge NATIVE_MANAGED_RUNTIMES must be a Set");
assert.ok(NATIVE_MANAGED_RUNTIMES.size > 0, "bridge NATIVE_MANAGED_RUNTIMES must be non-empty");

// 2. Parse service-side set from api_v2.py
const apiV2Path = path.join(repoRoot, "service", "routers", "api_v2.py");
const apiV2Text = fs.readFileSync(apiV2Path, "utf-8");
const match = apiV2Text.match(/^_NATIVE_MANAGED_RUNTIMES\s*=\s*\{([^}]+)\}/m);
assert.ok(match, "could not locate _NATIVE_MANAGED_RUNTIMES in service/routers/api_v2.py");
const serviceSet = new Set(
  match[1]
    .split(",")
    .map((s) => s.trim().replace(/^["']|["']$/g, ""))
    .filter(Boolean),
);

// 3. Sets must match exactly
const bridgeArr = [...NATIVE_MANAGED_RUNTIMES].sort();
const serviceArr = [...serviceSet].sort();
assert.deepEqual(
  bridgeArr,
  serviceArr,
  `bridge NATIVE_MANAGED_RUNTIMES (${bridgeArr.join(", ")}) does not match service-side _NATIVE_MANAGED_RUNTIMES (${serviceArr.join(", ")}). Both must be updated together when adding a runtime that uses native RPC dispatch.`,
);

console.log("native-managed-sync.test.js: all assertions passed");
