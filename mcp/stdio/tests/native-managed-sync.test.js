#!/usr/bin/env node
// Regression test for the bridge-side vs service-side
// NATIVE_MANAGED_RUNTIMES drift that operator hit 2026-05-22.
// When d87457b added hermes to the SERVICE-side set
// (_NATIVE_MANAGED_RUNTIMES in service/control_plane.py) but missed
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

function parsePythonRuntimeSet(filePath, pattern, label) {
  const text = fs.readFileSync(filePath, "utf-8");
  const match = text.match(pattern);
  assert.ok(match, `could not locate ${label}`);
  return new Set(
    match[1]
      .split(",")
      .map((s) => s.trim().replace(/^["']|["']$/g, ""))
      .filter(Boolean),
  );
}

// 2. Parse service-side sets from service/control_plane.py and service/db.py
const controlPlanePath = path.join(repoRoot, "service", "control_plane.py");
const dbPath = path.join(repoRoot, "service", "db.py");
const serviceSet = parsePythonRuntimeSet(
  controlPlanePath,
  /^_NATIVE_MANAGED_RUNTIMES\s*=\s*\{([^}]+)\}/m,
  "_NATIVE_MANAGED_RUNTIMES in service/control_plane.py",
);
const dbSet = parsePythonRuntimeSet(
  dbPath,
  /^_NATIVE_MANAGED_RUNTIMES\s*=\s*\(([^)]+)\)/m,
  "_NATIVE_MANAGED_RUNTIMES in service/db.py",
);

// 3. Sets must match exactly
const bridgeArr = [...NATIVE_MANAGED_RUNTIMES].sort();
const serviceArr = [...serviceSet].sort();
const dbArr = [...dbSet].sort();
assert.deepEqual(
  bridgeArr,
  serviceArr,
  `bridge NATIVE_MANAGED_RUNTIMES (${bridgeArr.join(", ")}) does not match service-side _NATIVE_MANAGED_RUNTIMES (${serviceArr.join(", ")}). Both must be updated together when adding a runtime that uses native RPC dispatch.`,
);
assert.deepEqual(
  dbArr,
  serviceArr,
  `service/db.py native-managed backfill set (${dbArr.join(", ")}) does not match service/control_plane.py _NATIVE_MANAGED_RUNTIMES (${serviceArr.join(", ")}). Stale managed agents would miss capability repair after migrations.`,
);

console.log("native-managed-sync.test.js: all assertions passed");
