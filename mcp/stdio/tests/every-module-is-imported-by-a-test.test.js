// Every bridge and dashboard module must be imported by some test.
//
// THE STANDING STANDARD, FINALLY ENFORCED FOR ALL MODULES. The reviewer's rule for this series is
// "byte-identical bodies + the new module EXPORTS what it extracts + real unit tests that call it". The
// third clause was gated only for modules named as a `moved to` DESTINATION
// (`moved-names-resolve.test.js`). That misses everything extracted without a marker — which is every
// *-tools.mjs module, because a tool name is not a declaration and those slices correctly left only their
// register call. `send-tools.mjs` was the most recent: it would have satisfied every gate in the repo with
// no test at all.
//
// A FLOOR, NOT COVERAGE, and it says so where someone will read it. "Some test imports it" does not mean
// the module is meaningfully exercised — a test could import it and assert nothing. What it catches is the
// case that actually happens: a module extracted in a hurry with no test file, which is invisible to every
// other gate here.
//
// THE REMAINING EXCEPTIONS ARE PRE-EXISTING DEBT, held at a list that may only shrink, on the same pattern
// `RECONCILER_BORROW_CEILING` and `no-unwatched-oversized-file.test.js`. None was created by the v0.5.4
// series; a hard ban would fail the suite for work nobody here did and teach the next person to weaken the
// gate instead of paying it down.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const MODULE_DIRS = ["mcp/stdio", "service/new_dashboard"];
// Test files live in BOTH shapes: a `tests/` directory under mcp/stdio, and `*.test.mjs` sitting beside
// the module in mcp/stdio and service/new_dashboard. Collecting only the directory made this gate report
// `claude-stop-gate.js` as untested when `mcp/stdio/claude-stop-gate.test.mjs` sits right next to it — a
// blind spot in the gate written to catch blind spots, found on its first run.
const TEST_DIRS = ["mcp/stdio/tests", "mcp/stdio", "service/new_dashboard"];

//: MEASURED 2026-08-14. Every one predates this series. Removing a name here — by writing it a test — is
//: the goal; adding one is not, which is what the last assertion enforces.
const UNTESTED_BACKLOG = [
  "mcp/stdio/hermes-daemon-cli.js",
  "mcp/stdio/load-env.js",
  "mcp/stdio/runtimes-helpers.js",
  "mcp/stdio/runtimes-hermes.js",
  "mcp/stdio/runtimes-opencode.js",
  "mcp/stdio/runtimes-pi.js",
  "mcp/stdio/runtimes-rpc.js",
  "mcp/stdio/usage-preflight.js",
];

function modules() {
  const out = [];
  for (const dir of MODULE_DIRS) {
    for (const name of readdirSync(path.join(REPO, dir))) {
      if (!/\.(js|mjs)$/.test(name) || /\.test\.(js|mjs)$/.test(name)) continue;
      out.push(`${dir}/${name}`);
    }
  }
  return out.sort();
}

const SELF = path.basename(fileURLToPath(import.meta.url));

function testSources() {
  const out = [];
  for (const dir of TEST_DIRS) {
    for (const name of readdirSync(path.join(REPO, dir))) {
      if (!/\.(js|mjs)$/.test(name)) continue;
      // THIS FILE IS EXCLUDED, and it has to be. `UNTESTED_BACKLOG` below spells out the very paths the
      // matcher looks for, so counting this file as a test source made every backlog entry look tested —
      // the gate silently exonerating exactly the modules it exists to track. Test 3 caught it: entry-is-
      // still-untested disagreed with no-unexpected-untested, and only one of them could be right.
      if (name === SELF) continue;
      if (dir.endsWith("/tests") || /\.test\.(js|mjs)$/.test(name)) {
        out.push(readFileSync(path.join(REPO, dir, name), "utf-8"));
      }
    }
  }
  return out;
}

/** Modules no test file references by path. */
function untestedModules() {
  const sources = testSources();
  return modules().filter((rel) => {
    const base = rel.split("/").pop();
    return !sources.some((src) => src.includes(`/${base}`) || src.includes(`"${base}`));
  });
}

test("the scan sees the modules and the tests — neither side is empty", () => {
  // Anti-vacuity in both directions. An empty module list passes everything; an empty test list fails
  // everything and would be reverted rather than investigated.
  assert.ok(modules().length > 100, `expected many modules, found ${modules().length}`);
  assert.ok(testSources().length > 100, `expected many test files, found ${testSources().length}`);
});

test("no module is untested except the recorded backlog", () => {
  const unexpected = untestedModules().filter((m) => !UNTESTED_BACKLOG.includes(m));
  assert.deepEqual(
    unexpected, [],
    "these modules have no test importing them:\n  " + unexpected.join("\n  ")
      + "\nThe standard for this series is a real unit test that CALLS what a module exports. If a module "
      + "genuinely cannot be tested — it starts a process, or needs a role flag — say so in its test file "
      + "and test what can be reached, as single-agent-teardown.mjs and managed-teardown-sweeps.mjs do.",
  );
});

test("THE BACKLOG MAY ONLY SHRINK — a name still listed here must still be untested", () => {
  // The ratchet's other half. A backlog entry that has since been tested is slack: it would let a LATER
  // module of the same name slip in unnoticed, and it misreports how much debt is left.
  const stillUntested = new Set(untestedModules());
  const paid = UNTESTED_BACKLOG.filter((m) => !stillUntested.has(m));
  assert.deepEqual(
    paid, [],
    "these are now tested — delete them from UNTESTED_BACKLOG in the same commit:\n  " + paid.join("\n  "),
  );
});

test("every backlog entry actually exists", () => {
  // A deleted file left in the list would quietly shrink the gate's reach.
  const all = new Set(modules());
  const missing = UNTESTED_BACKLOG.filter((m) => !all.has(m));
  assert.deepEqual(missing, [], `listed but not present: ${missing.join(", ")}`);
});

test("the modules this series created are NOT in the backlog", () => {
  // The line that matters for new work: v0.5.4 extracted dozens of modules and every one of them carries
  // tests. The backlog is for code that predates the series, and it must not become a landing place for
  // new extractions.
  for (const recent of [
    "mcp/stdio/send-tools.mjs",
    "mcp/stdio/spawn-triggered-agent.mjs",
    "mcp/stdio/managed-teardown-sweeps.mjs",
    "mcp/stdio/claim-failure-tracker.mjs",
    "service/new_dashboard/api-client.mjs",
    "service/new_dashboard/shared-files.mjs",
  ]) {
    assert.ok(!UNTESTED_BACKLOG.includes(recent), `${recent} is new work and must carry its own tests`);
    assert.ok(!untestedModules().includes(recent), `${recent} must be imported by a test`);
  }
});
