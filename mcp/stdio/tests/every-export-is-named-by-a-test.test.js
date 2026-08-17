// Every EXPORT of a bridge or dashboard module must be named by some test.
//
// A FINER FLOOR THAN THE ONE BESIDE IT. `every-module-is-imported-by-a-test.test.js` asks whether a
// module has any test at all, which catches "extracted in a hurry with no test file". It cannot see
// the next case down: a module with a test that exercises two of its nine exports, where the other
// seven are as untested as if the file had no test — and are invisible, because the module reads as
// covered.
//
// MEASURED FIRST, like every other ratchet here: 220 modules, 990 exports, 42 named by no test.
// Thin and spread out — mostly one or two per module — which is what makes them easy to leave. The
// backlog below started as those 42 and MAY ONLY SHRINK: 42 -> 24 -> 14, the last step being the
// eight dashboard entries paid down together in `exported-vocabularies.test.mjs` and then the seven
// launch helpers in `runtime-launch-helpers.test.js`, both on 2026-08-17. Seven entries for four
// functions: `spawnProcess` and `defaultPiCommand` are re-exported, so one test clears several rows
// — and the re-export chain is itself asserted there, because a controller importing a DIFFERENT
// function of the same name is what that chain exists to prevent.
// `wrapper-pool.js#disposeAll` came off the same day and is why this ratchet earns its keep: the
// export it had recorded as untested DISPOSED NOTHING — it cleared the pool and then looked its keys
// up in the map it had just emptied. Six of the nine tests written for it fail against the old body.
// `terminal-text.js#OSC_NOISE_RE` came off by DELETION rather than by test: it had one reader, in its
// own file, and it is a GLOBAL regex — a public `/g` constant carries a mutable lastIndex, so an
// importer calling `.test()` on it gets alternating answers. Un-exporting is the fix; paying an entry
// down that way is legitimate and this gate enforces it, because an export that no longer exists stops
// being untested and the third test below then demands the row go.
// Seven of those eight were exported CONSTANTS, which is the shape worth noticing: a vocabulary or
// a bound that some other module reads, with nothing asserting the two still agree.
//
// WHAT THIS PROVES AND WHAT IT DOES NOT. "A test names it" is not "a test asserts anything useful
// about it": a name mentioned in a docstring counts, and a name that happens to appear in an
// unrelated file counts. It is deliberately generous in the same way as its neighbour and as
// `test_every_refusal_is_exercised.py`, because the case it exists to catch is the export nothing
// mentions anywhere.
//
// THE PARSER IS THE REPO'S OWN. `exportedNames` from `tests/missing-imports.mjs` already handles the
// forms this codebase uses, including `export const` blocks and re-exports; a fresh regex here would
// be the fourth hand-rolled JS parser in this series and the previous three were each wrong.
//
// THE WORD-BOUNDARY ESCAPES ARE BUILT FROM String.raw, deliberately. The scratchpad version of this
// measurement was written through a heredoc that collapsed "\\b" to "\b" — which inside a template
// literal is the BACKSPACE character, so every regex was <BS>name<BS>, matched nothing, and reported
// ALL 990 exports as untested. A plausible-looking number from a mangled escape is this series'
// most repeated measurement failure.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { exportedNames } from "./missing-imports.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const SELF = path.basename(fileURLToPath(import.meta.url));

const MODULE_DIRS = ["mcp/stdio", "service/new_dashboard"];
//: Where a test that RUNS lives, per the two runners: `mcp/stdio/tests/*.test.js` via run-all.mjs,
//: and `service/new_dashboard/*.test.mjs` via `node --test`. The extensions are opposite, which is
//: how a file can look like a test and never execute — the reason its neighbour encodes the same
//: pair rather than globbing.
const TEST_SOURCES = [
  ["mcp/stdio/tests", (name) => name.endsWith(".test.js")],
  ["service/new_dashboard", (name) => name.endsWith(".test.mjs")],
];

const WORD_BOUNDARY = String.raw`\b`;

//: MEASURED 2026-08-16, `module#export`. MAY ONLY SHRINK: the second test below fails if an entry
//: here is now named by a test, so paying one down means deleting its line in the same commit.
//: Nothing was chosen for this list — it is the whole of what the scan found.
const UNTESTED_EXPORT_BACKLOG = [
  "mcp/stdio/console-tools.mjs#registerConsoleTools",
  "mcp/stdio/runtimes-codex.js#discoverCodexLiveBinding",
  "mcp/stdio/runtimes.js#discoverCodexLiveBinding",
];

function moduleFiles() {
  const out = [];
  for (const dir of MODULE_DIRS) {
    for (const name of readdirSync(path.join(REPO, dir))) {
      if (!/\.(js|mjs)$/.test(name) || /\.test\.(js|mjs)$/.test(name)) continue;
      out.push(`${dir}/${name}`);
    }
  }
  return out.sort();
}

// THIS FILE IS EXCLUDED, and it has to be: the backlog above spells out the very identifiers the
// matcher searches for, so counting this file as a test source would report every backlogged export
// as covered — the gate exonerating exactly what it tracks. Its neighbour records the same trap
// after making the mistake.
function testBlob() {
  const parts = [];
  for (const [dir, keep] of TEST_SOURCES) {
    for (const name of readdirSync(path.join(REPO, dir))) {
      if (!keep(name) || name === SELF) continue;
      parts.push(readFileSync(path.join(REPO, dir, name), "utf-8"));
    }
  }
  return parts.join("\n");
}

function untestedExports() {
  const blob = testBlob();
  const out = [];
  for (const rel of moduleFiles()) {
    const source = readFileSync(path.join(REPO, rel), "utf-8");
    for (const name of exportedNames(source)) {
      if (!name || name === "default") continue;
      const pattern = new RegExp(WORD_BOUNDARY + name.replace(/[$]/g, "\\$&") + WORD_BOUNDARY);
      if (!pattern.test(blob)) out.push(`${rel}#${name}`);
    }
  }
  return out.sort();
}

test("the scan sees modules, exports and tests — no side is empty", () => {
  // Anti-vacuity in every direction. An empty module list passes everything; an empty blob fails
  // everything and gets reverted rather than investigated; a parser returning no names does both.
  const modules = moduleFiles();
  assert.ok(modules.length > 100, `expected many modules, found ${modules.length}`);
  const blob = testBlob();
  assert.ok(blob.length > 500_000, `test blob read as ${blob.length} chars`);
  const total = modules.reduce(
    (n, rel) => n + [...exportedNames(readFileSync(path.join(REPO, rel), "utf-8"))].length, 0);
  assert.ok(total > 500, `expected many exports, parsed ${total}`);
  // A name no test can contain must be reported, or the matcher matches everything.
  assert.ok(!new RegExp(WORD_BOUNDARY + "zzNoTestNamesThisIdentifierzz" + WORD_BOUNDARY).test(blob));
  // And a name every test tree contains must NOT be reported, or it matches nothing — which is what
  // a collapsed "\\b" escape did to the scratchpad version of this scan.
  assert.ok(new RegExp(WORD_BOUNDARY + "assert" + WORD_BOUNDARY).test(blob));
});

test("no export is untested except the recorded backlog", () => {
  const unexpected = untestedExports().filter((e) => !UNTESTED_EXPORT_BACKLOG.includes(e));
  assert.deepEqual(
    unexpected, [],
    "these exports are named by no test:\n  " + unexpected.join("\n  ")
      + "\nAn export is the unit another module depends on. If one genuinely cannot be tested — it "
      + "starts a process, or reads a platform-specific path — say so in the module's test file and "
      + "test what can be reached, rather than adding it here.",
  );
});

test("THE BACKLOG MAY ONLY SHRINK — an entry still listed must still be untested", () => {
  const stillUntested = new Set(untestedExports());
  const paid = UNTESTED_EXPORT_BACKLOG.filter((e) => !stillUntested.has(e));
  assert.deepEqual(
    paid, [],
    "these are now named by a test — delete them from UNTESTED_EXPORT_BACKLOG in the same commit:\n  "
      + paid.join("\n  "),
  );
});

test("every backlog entry still exists", () => {
  // A renamed or deleted export left in the list would quietly shrink the gate's reach, and would
  // read as debt that is still owed.
  const live = new Set();
  for (const rel of moduleFiles()) {
    for (const name of exportedNames(readFileSync(path.join(REPO, rel), "utf-8"))) {
      live.add(`${rel}#${name}`);
    }
  }
  const gone = UNTESTED_EXPORT_BACKLOG.filter((e) => !live.has(e));
  assert.deepEqual(gone, [], "backlog names exports that no longer exist:\n  " + gone.join("\n  "));
});
