// A name a carrier gave away must be imported back if the carrier still uses it.
//
// WHAT THIS RETIRES: the manual post-relocation audit. Every extraction batch in v0.5.4 ended with me
// re-reading each carrier by eye to check nothing had been left dangling. That audit found a real defect
// once and it is not repeatable — this is the same question asked by a gate.
//
// WHY IT IS NEEDED AT ALL: the Python side runs a symtable undefined-name sweep on every slice; JS has no
// equivalent here, and one was attempted twice and withdrawn (a general resolver produced 1,362 false
// positives; regex literals containing backticks defeat any scanner without a real parser). `node --check`
// does not help — it PARSES, it does not resolve, so a file referencing an undefined name passes it and
// throws on the first real call. This series shipped exactly that once: `SAFETY_HEADER is not defined`,
// found by a reviewer exercising the branch rather than by any test.
//
// THE NARROW QUESTION IS ANSWERABLE. Rather than resolve every identifier, ask only about the names the
// carriers THEMSELVES declare they gave away, in their own `// X moved to ./y in v0.5.4.` markers. That is
// a closed list the extraction process already maintains, so there is no guessing and no false-positive
// budget to manage.
//
// Over-counting is the safe direction and is deliberate: comments are stripped (a marker naming a function
// must not count as a use of it) but string literals are NOT, so a name mentioned only in a string reads
// as used. That can raise a false alarm; it cannot let a real dangling reference through.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// tests -> stdio -> mcp -> repo root. Asserted below rather than trusted: an off-by-one here would make
// the production scan fail loudly (it does — that is how this was caught), but a wrong path that happened
// to exist would be worse.
const REPO = path.resolve(HERE, "..", "..", "..");

const MARKER_LINE = /^\/\/\s*[A-Za-z_$][\w$]*(?:\s*\/\s*[A-Za-z_$][\w$]*)?\s+moved to /m;

/** Names the source says it moved away, which it still references and neither imports nor re-declares. */
export function unresolvedMovedNames(source) {
  const lines = source.split("\n");

  const moved = new Set();
  for (const line of lines) {
    // Markers name one function, or two when a pair moved together on one line.
    const m = /^\/\/\s*([A-Za-z_$][\w$]*)(?:\s*\/\s*([A-Za-z_$][\w$]*))?\s+moved to /.exec(line.trim());
    if (m) {
      moved.add(m[1]);
      if (m[2]) moved.add(m[2]);
    }
  }

  const imported = new Set();
  for (const m of source.matchAll(/import\s*\{([^}]*)\}\s*from/g)) {
    // STRIP LINE COMMENTS INSIDE THE BLOCK FIRST. `hermes-managed-host.js` writes them on the OPENER —
    // `import {  // v0.5.4: neutral owner` — and with no comma between the comment and the first name,
    // splitting on commas glues them together and the name is never seen. That hid three real imports and
    // made this gate report them as dangling. The identical trap cost four rounds of debugging on this
    // same file earlier in the series with a different parser; it is a house style here, not an oddity.
    const body = m[1].replace(/\/\/[^\n]*/g, "");
    for (const raw of body.split(",")) {
      // `a as b` binds b — the local name is what the body uses.
      const name = raw.trim().split(/\s+as\s+/).pop().trim();
      if (name) imported.add(name);
    }
  }

  const declared = new Set();
  for (const line of lines) {
    const m = /^(?:export\s+)?(?:async\s+)?(?:function|const|let|var)\s+([\w$]+)\b/.exec(line);
    if (m) declared.add(m[1]);
  }

  const code = source.replace(/^\s*\/\/[^\n]*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");

  return [...moved].sort().filter((name) => {
    if (imported.has(name) || declared.has(name)) return false;
    return new RegExp(`(?<![\\w$.])${name}(?![\\w$])`).test(code);
  });
}

// --- the predicate itself, against synthetic sources -----------------------
//
// Exercised on fixtures rather than only on production, because a gate whose failing branch has never run
// is a gate nobody has tested. Four of these anti-vacuity checks caught real problems earlier in v0.5.4.

test("it REPORTS a moved name the carrier still calls but never imports", () => {
  const src = [
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function caller() { return doThing(1); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(src), ["doThing"]);
});

test("importing it back clears the report, including under an alias", () => {
  const plain = [
    "import { doThing } from './thing.mjs';",
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function caller() { return doThing(1); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(plain), []);

  // `x as doThing` binds `doThing`; keying on the left-hand name would report a false dangle.
  const aliased = [
    "import { original as doThing } from './thing.mjs';",
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function caller() { return doThing(1); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(aliased), []);
});

test("a comment ON THE IMPORT OPENER does not hide the first name", () => {
  // The house style in hermes-managed-host.js. With no comma between the comment and the first name, a
  // naive comma split glues them into one token and the import is never registered — which made this gate
  // report three perfectly good imports as dangling references the first time it ran wide.
  const src = [
    "import {  // v0.5.4: neutral owner",
    "  doThing,",
    "  other,",
    "} from './thing.mjs';",
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function caller() { return doThing(1) + other(); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(src), []);
});

test("a moved name the carrier no longer uses is fine — that is the goal state", () => {
  const src = [
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function caller() { return somethingElse(); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(src), []);
});

test("the MARKER itself is not counted as a use", () => {
  // Comments are stripped first. Without that, every marker would report its own name forever — the gate
  // would fire on every correct slice and be suppressed within a day.
  const src = "// doThing moved to ./thing.mjs in v0.5.4.";
  assert.deepEqual(unresolvedMovedNames(src), []);
});

test("a PROPERTY of the same name is not a use of the moved binding", () => {
  // `obj.doThing()` resolves on the object, not the module scope. Reporting it would be a false alarm on
  // ordinary code, and this is the case a naive substring scan gets wrong.
  const src = [
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function caller(obj) { return obj.doThing(1); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(src), []);
});

test("a name that moved and was then RE-DECLARED locally is not reported", () => {
  // Not a dangling reference — it is a stale duplicate, which is a different defect with its own check.
  // Reporting it here would mislabel it.
  const src = [
    "// doThing moved to ./thing.mjs in v0.5.4.",
    "function doThing() { return 1; }",
    "function caller() { return doThing(); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(src), []);
});

test("both names on a two-name marker are checked", () => {
  const src = [
    "// alpha / beta moved to ./pair.mjs in v0.5.4.",
    "function caller() { return alpha() + beta(); }",
  ].join("\n");
  assert.deepEqual(unresolvedMovedNames(src), ["alpha", "beta"]);
});

// --- the production scan ---------------------------------------------------

// CARRIERS ARE DISCOVERED, NOT LISTED.
//
// The first version of this gate named `server.js` and `app.js`. That is the silent-shrink class the repo
// already has a rule about — a gate whose PURPOSE says "any carrier" but whose CODE names two files. It
// was missing `hermes-managed-host.js`, which carries 57 markers of its own. A hardcoded list also cannot
// notice the next carrier, which is exactly when a gate is most needed.
const SOURCE_DIRS = ["mcp/stdio", "service/new_dashboard"];

function sourceFiles() {
  const out = [];
  for (const dir of SOURCE_DIRS) {
    for (const name of fs.readdirSync(path.join(REPO, dir))) {
      if (!/\.(js|mjs)$/.test(name)) continue;
      if (/\.test\.(js|mjs)$/.test(name)) continue;
      out.push(`${dir}/${name}`);
    }
  }
  return out;
}

const CARRIERS = sourceFiles().filter((rel) => MARKER_LINE.test(fs.readFileSync(path.join(REPO, rel), "utf-8")));

test("every name the carriers gave away still resolves", () => {
  // Reading both trees from one test on purpose: one gate, one rule. Two copies would be the forked-policy
  // class this series exists to remove, and the oversized-file gates already read one shared policy across
  // the whole repo for the same reason.
  for (const rel of CARRIERS) {
    const file = path.join(REPO, rel);
    // A missing carrier must FAIL, never skip: a check that gathered no evidence must not read as a pass.
    assert.ok(fs.existsSync(file), `${rel} not found — this gate cannot verify anything`);
    const unresolved = unresolvedMovedNames(fs.readFileSync(file, "utf-8"));
    assert.deepEqual(unresolved, [], `${rel} uses these moved names without importing them: ${unresolved.join(", ")}`);
  }
});

test("the scan is actually looking at something — and at EVERY carrier, not a hardcoded pair", () => {
  // Anti-vacuity, and the fix for this gate's own first version. If a rename broke the marker format the
  // scan above would pass on an empty set forever; if discovery broke, it would silently cover fewer files.
  assert.ok(CARRIERS.length >= 3,
    `expected at least three carriers to be DISCOVERED, found ${CARRIERS.length}: ${CARRIERS.join(", ")}`);
  for (const expected of ["mcp/stdio/server.js", "mcp/stdio/hermes-managed-host.js", "service/new_dashboard/app.js"]) {
    assert.ok(CARRIERS.includes(expected), `${expected} carries markers and must be discovered`);
  }
  let total = 0;
  for (const rel of CARRIERS) {
    total += [...fs.readFileSync(path.join(REPO, rel), "utf-8")
      .matchAll(new RegExp(MARKER_LINE.source, "gm"))].length;
  }
  assert.ok(total > 150, `expected the carriers to declare many moved names, found ${total}`);
});
