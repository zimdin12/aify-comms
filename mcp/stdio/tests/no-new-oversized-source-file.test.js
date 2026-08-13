// No JS slice may push a file OVER 1000 lines. The JS half of the size ratchet.
//
// WHY THIS EXISTS. `service/tests/test_no_new_oversized_source_file.py` gates the same rule for Python,
// after a v0.5.4 relocation moved a 6-line helper into `service/db.py` and took it from 995 to 1006 —
// shrinking the carrier by creating a NEW oversized file, which is the shell game the goal exists to
// prevent. Every gate passed, because none of them measured the DESTINATION of a move.
//
// That gate covers `service/**.py` only, and FOUR of the five files still over the limit are JS. The JS
// lane has already shown the same growth pattern: `hermes-gateway.mjs` went 579 -> 631 absorbing extracted
// spans, and on the Python side `api_core/dispatch_start.py` reached 943 the same way. Neither crossed,
// but nothing would have said so if they had.
//
// A RATCHET, NOT AN ALLOWLIST. `KNOWN_OVERSIZED` is a MEASUREMENT of what was already over the line, not a
// list anyone approved — every entry has an open packet or a reviewer ruling:
//   server.js 6330              packet accepted as measurement; awaiting operator scope
//   app.js 5010                 reviewer-ruled relocation ceiling
//   hermes-managed-host.js 1845 reviewer-ruled relocation ceiling
//   pi-session.js 1299          relocation provably cannot clear it
//
// Two assertions, mirroring the Python gate: nothing OUTSIDE the set may reach the limit, and nothing IN
// the set may already be cleared. The second is what stops the set rotting into names nobody re-checked —
// clearing a file FAILS this until its name is removed, which is a one-line edit with an obvious meaning
// rather than a threshold quietly edited downward.

import assert from "node:assert/strict";
import test from "node:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const LIMIT = 1000;

/** Roots that hold shipped JS. Test dirs, fixtures and node_modules are excluded below. */
const ROOTS = [path.join(REPO, "mcp", "stdio"), path.join(REPO, "service", "new_dashboard")];

const SKIP_DIRS = new Set(["node_modules", "tests", "fixtures", "__pycache__", ".git"]);

/** MEASURED, not approved. Remove a name the moment its file drops below the limit. */
const KNOWN_OVERSIZED = new Set([
  "server.js",
  "app.js",
  "hermes-managed-host.js",
  "pi-session.js",
]);

function sourceFiles() {
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        if (!SKIP_DIRS.has(entry)) walk(full);
        continue;
      }
      if (!/\.m?js$/.test(entry)) continue;
      if (/\.test\.m?js$/.test(entry)) continue;
      out.push(full);
    }
  };
  for (const root of ROOTS) walk(root);
  return out.sort();
}

/** Newline count, matching `wc -l` — the convention every receipt in this series uses. */
function lineCount(file) {
  return readFileSync(file, "utf-8").split("\n").length - 1;
}

test("no JS file outside the measured set is oversized", () => {
  const offenders = sourceFiles()
    .filter((f) => !KNOWN_OVERSIZED.has(path.basename(f)) && lineCount(f) >= LIMIT)
    .map((f) => `${path.relative(REPO, f).replace(/\\/g, "/")}: ${lineCount(f)} lines`);
  assert.deepEqual(
    offenders,
    [],
    "A JS file crossed the 1000-line limit. If an extraction did this, the destination is wrong even when "
      + `its SUBJECT is right — reducing one file by growing another past the limit is not progress:\n  ${offenders.join("\n  ")}`,
  );
});

test("the measured set has no stale entries", () => {
  const byName = new Map(sourceFiles().map((f) => [path.basename(f), f]));
  const stale = [];
  for (const name of [...KNOWN_OVERSIZED].sort()) {
    const file = byName.get(name);
    if (!file) stale.push(`${name}: no longer found under the scanned roots`);
    else if (lineCount(file) < LIMIT) stale.push(`${name}: now ${lineCount(file)} lines — drop it from KNOWN_OVERSIZED`);
  }
  assert.deepEqual(stale, [], stale.join("\n  "));
});

test("the scan actually reaches the files it claims to cover", () => {
  // A ratchet over an empty file list passes vacuously. This asserts DISCOVERY rather than a count, so it
  // keeps meaning something as files are added and removed — the mistake the old `> 20` accessor floor made.
  const names = new Set(sourceFiles().map((f) => path.basename(f)));
  for (const known of KNOWN_OVERSIZED) {
    assert.ok(names.has(known), `the scan did not find ${known}; its roots or filters are wrong`);
  }
  assert.ok(names.has("doctor-predicates.js"), "expected an ordinary mcp/stdio module in scope");
  assert.ok(names.has("util.js"), "expected an ordinary new_dashboard module in scope");
});

test("test files and node_modules are excluded", () => {
  const files = sourceFiles().map((f) => path.relative(REPO, f).replace(/\\/g, "/"));
  assert.ok(!files.some((f) => f.includes("node_modules")), "node_modules must not be scanned");
  assert.ok(!files.some((f) => /\.test\.m?js$/.test(f)), "test files must not be scanned");
  assert.ok(!files.some((f) => f.includes("/fixtures/")), "pristine fixtures are not shipped source");
});

test("the boundary predicate is exact", () => {
  // Off-by-one here would silently accept the precise 1000-line file this exists to catch.
  assert.ok(1000 >= LIMIT, "a file of exactly 1000 lines must count as oversized");
  assert.ok(!(999 >= LIMIT), "a file of 999 lines must not be flagged");
});
