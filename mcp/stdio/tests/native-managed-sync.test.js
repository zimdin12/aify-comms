#!/usr/bin/env node
// Regression test for the bridge-side vs service-side
// NATIVE_MANAGED_RUNTIMES drift that operator hit 2026-05-22.
// When d87457b added hermes to the SERVICE-side set but missed
// the BRIDGE-side set (NATIVE_MANAGED_RUNTIMES in dispatch-execution.js),
// the bridge silently stopped claiming hermes managed dispatches.
// This test prevents that class of drift recurring.
//
// v0.5.4: the service-side authority is now FOUND, not named. This probe used to open
// `service/control_plane.py` by path, which is the same defect that broke
// virtual-rpc-runtimes-sync.test.js twice — and worse than breaking, a named file that no longer
// holds the pattern makes the probe pass while guarding nothing.
//
// Note this subject has TWO deliberate declarations, so "exactly one owner" is the WRONG assertion
// here: `service/db.py` keeps its own copy as a post-migration backfill set, and reconciling the two
// is precisely what this test is for. So: require exactly two owners, require db.py to be one of
// them (its copy belongs to db.py's own responsibility and is not expected to move), and let the
// other be wherever the refactor has put it. A third copy appearing fails loudly.

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

// 2. Locate every service-side declaration by searching the tree, then parse each in place.
const DECL = /^_NATIVE_MANAGED_RUNTIMES\s*=\s*[{(]([^})]+)[})]/m;
function pythonFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name === "tests") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...pythonFiles(full));
    else if (entry.name.endsWith(".py")) out.push(full);
  }
  return out;
}
const owners = pythonFiles(path.join(repoRoot, "service"))
  .filter((f) => DECL.test(fs.readFileSync(f, "utf-8")));
assert.equal(
  owners.length, 2,
  `_NATIVE_MANAGED_RUNTIMES must have exactly two service-side declarations (the authority and the `
    + `service/db.py backfill copy); found ${JSON.stringify(owners)}`,
);
const dbPath = owners.find((f) => f.endsWith(`${path.sep}db.py`));
assert.ok(
  dbPath,
  `service/db.py must still declare its _NATIVE_MANAGED_RUNTIMES backfill copy; owners were `
    + `${JSON.stringify(owners)}`,
);
const authorityPath = owners.find((f) => f !== dbPath);
const serviceSet = parsePythonRuntimeSet(authorityPath, DECL, `_NATIVE_MANAGED_RUNTIMES in ${authorityPath}`);
const dbSet = parsePythonRuntimeSet(dbPath, DECL, `_NATIVE_MANAGED_RUNTIMES in ${dbPath}`);

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
  `service/db.py native-managed backfill set (${dbArr.join(", ")}) does not match the service-side authority ${authorityPath} (${serviceArr.join(", ")}). Stale managed agents would miss capability repair after migrations.`,
);

console.log("native-managed-sync.test.js: all assertions passed");
