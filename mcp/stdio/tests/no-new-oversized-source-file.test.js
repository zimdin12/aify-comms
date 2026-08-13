// No product source file may exceed 1000 lines unless the reviewer allowlisted it. JS half.
//
// WHY THIS EXISTS. `service/tests/test_no_new_oversized_source_file.py` gates the same rule for Python,
// after a v0.5.4 relocation moved a 6-line helper into `service/db.py` and took it from 995 to 1006 —
// shrinking the carrier by creating a NEW oversized file, which is the shell game the goal exists to
// prevent. Every gate passed, because none of them measured the DESTINATION of a move.
//
// FOUR of the five allowlisted files are JS, and the JS lane has already shown the same growth pattern:
// `hermes-gateway.mjs` went 579 -> 631 absorbing extracted spans across eight slices, and on the Python
// side `api_core/dispatch_start.py` reached 943 the same way. Neither crossed, but nothing would have said
// so if they had.
//
// ONE SOURCE OF TRUTH. The exempt set lives in `oversized-allowlist.json` at the repo root and is read by
// both gates. It is POLICY-OWNED — set by the reviewer, not inferred from whatever is currently oversized.
// Duplicating the list here would be the forked-constant class this series has spent itself removing, and
// the two gates would drift into enforcing different policies.
//
// PATHS, NOT BASENAMES. The first version of this gate matched `path.basename`, which would have exempted
// any file called `app.js` or `server.js` anywhere in the tree.
//
// Both directions are asserted: nothing outside the allowlist may reach the limit, and nothing inside it
// may be missing or already cleared — so the list shrinks honestly instead of rotting into unchecked names.

import assert from "node:assert/strict";
import test from "node:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");

const POLICY = JSON.parse(readFileSync(path.join(REPO, "oversized-allowlist.json"), "utf-8"));
const LIMIT = POLICY.limit;
const ALLOWED = new Set(POLICY.allowed.map((e) => e.path));

/** Roots that hold shipped JS. Test dirs, fixtures and node_modules are excluded below. */
const ROOTS = [path.join(REPO, "mcp", "stdio"), path.join(REPO, "service", "new_dashboard")];
const SKIP_DIRS = new Set(["node_modules", "tests", "fixtures", "__pycache__", ".git"]);

function sourceFiles() {
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        if (!SKIP_DIRS.has(entry)) walk(full);
        continue;
      }
      if (!/\.m?js$/.test(entry) || /\.test\.m?js$/.test(entry)) continue;
      out.push(full);
    }
  };
  for (const root of ROOTS) walk(root);
  return out.sort();
}

const rel = (file) => path.relative(REPO, file).replace(/\\/g, "/");

/** Newline count, matching `wc -l` — the convention every receipt in this series uses. */
const lineCount = (file) => readFileSync(file, "utf-8").split("\n").length - 1;

test("no JS file outside the allowlist is oversized", () => {
  const offenders = sourceFiles()
    .filter((f) => !ALLOWED.has(rel(f)) && lineCount(f) >= LIMIT)
    .map((f) => `${rel(f)}: ${lineCount(f)} lines`);
  assert.deepEqual(
    offenders,
    [],
    "A JS file crossed the 1000-line limit. If an extraction did this, the destination is wrong even when "
      + "its SUBJECT is right. Adding it to oversized-allowlist.json is a REVIEWER decision, not a fix:\n  "
      + offenders.join("\n  "),
  );
});

test("the allowlist has no stale JS entries", () => {
  const stale = [];
  for (const entry of [...ALLOWED].filter((p) => /\.m?js$/.test(p)).sort()) {
    const full = path.join(REPO, entry);
    let count = null;
    try {
      count = lineCount(full);
    } catch {
      stale.push(`${entry}: no longer exists`);
      continue;
    }
    if (count < LIMIT) stale.push(`${entry}: now ${count} lines — drop it from oversized-allowlist.json`);
  }
  assert.deepEqual(stale, [], stale.join("\n  "));
});

test("the allowlist is path-keyed, not basename-keyed", () => {
  // Guards the hole the first version had: a basename match exempts every same-named file in the tree.
  for (const entry of ALLOWED) {
    assert.ok(entry.includes("/"), `${entry} looks like a basename; the allowlist is path-keyed`);
  }
});

test("both gates read the same allowlist file", () => {
  // The Python half resolves the same path. If either gate ever inlines its own copy, this is the note
  // that says why it must not: two lists drift, and each gate then enforces a different policy.
  assert.ok(POLICY.allowed.length > 0, "the allowlist must not be silently empty");
  for (const entry of POLICY.allowed) {
    assert.ok(entry.reason && entry.reason.trim(), `${entry.path} has no recorded reason`);
  }
});

test("the scan actually reaches the files it claims to cover", () => {
  // A gate over an empty file list passes vacuously. This asserts DISCOVERY rather than a count, so it
  // keeps meaning something as files come and go — the mistake the old `> 20` accessor floor made.
  const found = new Set(sourceFiles().map(rel));
  for (const entry of [...ALLOWED].filter((p) => /\.m?js$/.test(p))) {
    assert.ok(found.has(entry), `the scan did not find ${entry}; its roots or filters are wrong`);
  }
  assert.ok(found.has("mcp/stdio/doctor-predicates.js"), "expected an ordinary mcp/stdio module in scope");
  assert.ok(found.has("service/new_dashboard/util.js"), "expected an ordinary new_dashboard module in scope");
});

test("test files, fixtures and node_modules are excluded", () => {
  const files = sourceFiles().map(rel);
  assert.ok(!files.some((f) => f.includes("node_modules")), "node_modules must not be scanned");
  assert.ok(!files.some((f) => /\.test\.m?js$/.test(f)), "test files must not be scanned");
  assert.ok(!files.some((f) => f.includes("/fixtures/")), "pristine fixtures are not shipped source");
});

test("the boundary predicate is exact", () => {
  // Off-by-one here would silently accept the precise 1000-line file this exists to catch.
  assert.ok(1000 >= LIMIT, "a file of exactly 1000 lines must count as oversized");
  assert.ok(!(999 >= LIMIT), "a file of 999 lines must not be flagged");
});
