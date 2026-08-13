// No bridge module may import a name it never uses.
//
// THIS GATE EXISTS BECAUSE THE v0.5.4 DECOMPOSITION MANUFACTURED THEM. Every owner move takes a function out
// of `server.js`; the names that function alone used stay behind in the import block, referenced by nothing.
// Fourteen had accumulated in `server.js` by the time `comms_register` moved out — one of them
// `isClaudeTurnDetectorArmed`, whose disappearance from `server.js` is exactly what broke a test that named
// the file instead of the invariant. A dead import is not merely untidy: it is a false signal about what a
// module depends on, and the next person measuring an extraction's import surface will measure the lie.
//
// It is a real check, not a lint preference — `node --check` and all three suites pass with every one of
// them present.
//
// WHAT COUNTS AS USED is any occurrence of the identifier anywhere else in the file's CODE. Comments and
// module specifiers are stripped first. The specifier matters more than it sounds: without stripping it,
// `import fs from "fs"` can never be reported, because the quoted `"fs"` counts as a second occurrence of
// the identifier — and `fs`, `path` and `os` are the most common default imports in this bridge. The
// synthetic case at the bottom of this file is what found that; the first version of this gate silently
// exempted every one of them.
//
// The bias is otherwise one-directional: a false POSITIVE would fail the suite on working code, so anything
// ambiguous is treated as used.

import assert from "node:assert/strict";
import test from "node:test";

import { bridgeSources } from "./bridge-sources.mjs";

// The reconstruction proof in `hermes-gateway-extraction.test.js` strips this file's slice import blocks by
// matching their opener (`import {  // v0.5.4: moved out`) and closer (`} from "./x.mjs";`) lines verbatim,
// then byte-compares the remainder against a pristine fixture. Its import-block FORMAT is therefore
// load-bearing, and removing the dead names collapses multi-line blocks into single-line ones and breaks the
// proof — confirmed by doing it. Cleaning them means updating that proof in the same change, which is a
// deliberate slice and not a side effect of this sweep. Twenty-one names, all leftovers from earlier v0.5.4
// extractions; reported rather than hidden.
const PENDING_RECONSTRUCTION_PROOF = new Set(["hermes-managed-host.js"]);

function strip(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^.*?\/\/.*$/gm, (line) => line.split("//")[0]);
}

function deadImportsIn(text) {
  const withSpecifiers = strip(text);
  // Blank the module specifiers before counting uses, but keep them for PARSING the import statements.
  const src = withSpecifiers.replace(/(\bfrom\s*)"[^"]*"/g, '$1""');
  const names = new Set();
  for (const m of withSpecifiers.matchAll(/^import\s*\{([^}]*)\}\s*from\s*"[^"]+";/gm)) {
    for (const raw of m[1].split(",")) {
      const name = raw.trim().split(" as ").pop();
      if (name && /^\w+$/.test(name)) names.add(name);
    }
  }
  for (const m of withSpecifiers.matchAll(/^import\s+(\w+)\s+from\s+"/gm)) names.add(m[1]);
  return [...names].filter(
    (n) => (src.match(new RegExp(`(?<![\\w$.])${n}(?![\\w])`, "g")) || []).length < 2,
  ).sort();
}

test("no bridge module imports a name it never uses", () => {
  const offenders = bridgeSources()
    .filter(([file]) => !PENDING_RECONSTRUCTION_PROOF.has(file))
    .map(([file, text]) => [file, deadImportsIn(text)])
    .filter(([, dead]) => dead.length);
  assert.deepEqual(offenders, [],
    "dead imports: " + offenders.map(([f, d]) => `${f} (${d.join(", ")})`).join("; "));
});

test("the carve-out is REAL and is not quietly growing", () => {
  // A carve-out nobody counts becomes permanent. This pins the debt at what it actually is, so cleaning the
  // file makes this test fail and forces the exemption to be deleted rather than left behind — and so adding
  // a twenty-second dead name to it is a red test rather than a free pass.
  // TWENTY-ONE, counted by this file's own detector rather than by a one-off script. I first wrote 19 from
  // an inline `node -e` whose regex the shell had mangled, and it silently missed `os` and
  // `pickMostRecentSession`. The number in a gate must come from the gate.
  const [, text] = bridgeSources().find(([file]) => file === "hermes-managed-host.js");
  assert.equal(deadImportsIn(text).length, 21,
    "hermes-managed-host.js's dead-import count changed — clean it and drop the carve-out, or explain the new one");
});

test("the detector really detects — it finds a dead import in a synthetic module", () => {
  // Anti-vacuity. A scanner with a broken regex would report zero offenders and read as a clean bridge.
  const used = `import { a, b } from "./x.mjs";\nexport const y = a(b);\n`;
  assert.deepEqual(deadImportsIn(used), [], "a module using both imports must be clean");
  const dead = `import { a, b } from "./x.mjs";\nexport const y = a(1);\n`;
  assert.deepEqual(deadImportsIn(dead), ["b"], "…and one that drops `b` must be caught");
  const defaulted = `import fs from "fs";\nexport const y = 1;\n`;
  assert.deepEqual(deadImportsIn(defaulted), ["fs"], "default imports count too");
  // A name mentioned ONLY in a comment is still dead — comments are stripped before counting.
  const commented = `import { a } from "./x.mjs";\n// a is coming back next slice\nexport const y = 1;\n`;
  assert.deepEqual(deadImportsIn(commented), ["a"], "a comment-only mention does not rescue it");
  // Aliased imports are judged on the LOCAL name, which is the one that would be dangling.
  const aliased = `import { spawnSync as sp } from "node:child_process";\nexport const y = sp(1);\n`;
  assert.deepEqual(deadImportsIn(aliased), [], "an aliased import used under its alias is live");
});
