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
// Test files live in two shapes: a `tests/` directory under mcp/stdio, and `*.test.mjs` beside the module
// in service/new_dashboard. `mcp/stdio` itself is listed because this gate once counted top-level
// `*.test.mjs` files there — see `isRunByTheSuite`, which now refuses them. That widening was made to
// stop `claude-stop-gate.js` reporting as untested, and it worked by crediting two files that NO runner
// executed. Keeping the directory in the list with the runner check beside it is what makes the
// distinction visible rather than re-litigated.
const TEST_DIRS = ["mcp/stdio/tests", "mcp/stdio", "service/new_dashboard"];

//: MEASURED 2026-08-14, and now down to the two that CANNOT be import-tested. Both have ZERO exports:
//: they are scripts whose top level does the work, so importing one RUNS it — `hermes-daemon-cli.js`
//: drives a daemon and `usage-preflight.js` performs a quota check. "Write it a unit test" is not the
//: right answer for either; they need either an exported entry point or an end-to-end harness, which
//: is a change to the module rather than to this list. Recorded so the remaining two do not read as
//: the same kind of debt as the eight that were paid down.
const UNTESTED_BACKLOG = [
  "mcp/stdio/hermes-daemon-cli.js",
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

//: WHICH TEST FILES THE SUITES ACTUALLY EXECUTE, encoded from the two runners rather than assumed.
//: `mcp/stdio/tests/run-all.mjs` reads `tests`, `tests/adapters` and `tests/controllers` and filters
//: `*.test.js`; the dashboard suite is `node --test *.test.mjs` in `service/new_dashboard`. The two
//: use OPPOSITE extensions, which is precisely how a file can look like a test, sit beside its
//: module, and never run.
function isRunByTheSuite(dir, name) {
  if (dir === "mcp/stdio/tests") return name.endsWith(".test.js");
  if (dir === "service/new_dashboard") return name.endsWith(".test.mjs");
  // `mcp/stdio` itself is not a test directory for either runner: a `*.test.*` file sitting beside a
  // module there is discovered by nothing.
  return false;
}

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
      // A TEST FILE ONLY COUNTS IF SOMETHING RUNS IT. `mcp/stdio/claude-stop-gate.test.mjs` and its
      // e2e sibling sat at the TOP LEVEL as `*.test.mjs`, and `run-all.mjs` reads `tests/`,
      // `tests/adapters/` and `tests/controllers/` for `*.test.js` — so neither had EVER executed.
      // This gate counted them anyway and reported `claude-stop-gate.js` as covered; the comment
      // above TEST_DIRS records widening the collection to reach them, without anyone checking that
      // the runner could. Both files have since moved into `tests/` and both passed on their first
      // real run, but the rule is the point: crediting coverage to a file nothing executes is the
      // same false green this gate exists to prevent, one level up.
      if (!isRunByTheSuite(dir, name)) continue;
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

test("a test file only counts if a runner would actually execute it", () => {
  // ASSERTED DIRECTLY, because its EFFECT is currently a no-op: the two orphans that motivated it
  // have moved into `tests/`, so deleting the check changes no verdict today. A guard whose only
  // evidence is "the suite still passes" is indistinguishable from one that does nothing, and this
  // one exists for the next file that lands in the wrong place.
  assert.equal(isRunByTheSuite("mcp/stdio/tests", "x.test.js"), true, "run-all reads tests/*.test.js");
  assert.equal(
    isRunByTheSuite("mcp/stdio/tests", "x.test.mjs"), false,
    "run-all filters on .test.js — an .mjs in tests/ would sit there unrun",
  );
  assert.equal(
    isRunByTheSuite("mcp/stdio", "claude-stop-gate.test.mjs"), false,
    "THE ORPHAN SHAPE: a test beside its module in mcp/stdio is discovered by nothing",
  );
  assert.equal(isRunByTheSuite("mcp/stdio", "claude-stop-gate.test.js"), false, "…either extension");
  assert.equal(
    isRunByTheSuite("service/new_dashboard", "x.test.mjs"), true,
    "the dashboard suite is `node --test *.test.mjs`",
  );
  assert.equal(
    isRunByTheSuite("service/new_dashboard", "x.test.js"), false,
    "…and the two runners use OPPOSITE extensions, which is how a file can look like a test and "
    + "never run",
  );
});

test("the runner rule matches what run-all.mjs actually does", () => {
  // Read from the runner rather than trusted: if `run-all.mjs` changes its directories or its
  // filter, this rule is stale and the gate starts crediting files nothing executes again.
  const runner = readFileSync(path.join(REPO, "mcp/stdio/tests/run-all.mjs"), "utf-8");
  assert.match(runner, /\.endsWith\("\.test\.js"\)/, "run-all still filters on .test.js");
  assert.match(runner, /testDirs\s*=\s*\[\s*"tests"/, "run-all still reads tests/ first");
  for (const name of readdirSync(path.join(REPO, "mcp/stdio/tests"))) {
    if (!/\.test\.(js|mjs)$/.test(name)) continue;
    assert.ok(
      isRunByTheSuite("mcp/stdio/tests", name),
      `${name} is in tests/ but this rule says the runner skips it — it would never run`,
    );
  }
});
