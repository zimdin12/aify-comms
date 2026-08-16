// The same crash the bridge shipped four times, asked of the dashboard's 69 modules.
//
// `mcp/stdio/tests/no-missing-sibling-imports.test.js` was written after `aify-comms doctor` threw
// `ReferenceError: SERVICE_RUNTIME_PATHS is not defined` on its first line of real work, and it
// immediately found three more of the same shape in the bridge. Nothing had ever asked the question
// here — that gate's population is `mcp/stdio`.
//
// IT FOUND ONE, AND IT IS THE MOST REACHABLE OF THE FIVE. `run-inspector.mjs` renders
//
//     ${sourceMessage ? `<button … data-open-thread-message="${esc(messageId(sourceMessage))}">…` : ''}
//
// and imported `runPendingControlCount, runTargetAgent` from `./record-fields.mjs` — not
// `messageId`, which that module also exports. The bridge's four were all on error branches; this
// one is a NORMAL render path: any run with a source message. `node --check` parses it, and nothing
// renders this panel in a test.
//
// THE DETECTOR IS IMPORTED, NOT COPIED, across the two runtimes — the rule the dead-import gate
// states for the same pair of populations: "The Python side learned this the hard way: a sweep tool
// carrying its own regex deleted four LIVE imports because its copy had drifted from the gate's."
//
// Pointing it here exposed a real hole in the rule, exactly as extending the dead-import gate to the
// dashboard once did. Two, in fact, and both are fixed in `missing-imports.mjs` rather than worked
// around here:
//
//   * STRING AND TEMPLATE TEXT IS NOT CODE. `` `${apiOrigin}/api/v1/dashboard` `` in
//     `static-links.mjs` reported a missing import of an `api` the file never mentions. Blanking
//     template literals wholesale was not an option — `${messageId(sourceMessage)}` is the genuine
//     call above — so the text goes and the interpolations stay.
//   * A LINE-CONTINUATION BACKSLASH inside a template broke the delimiter match (`\\.` does not
//     span a newline), leaving an opening backtick to pair with a LATER one and exposing everything
//     between them. A regex that mis-pairs delimiters is worse than one that misses: the damage
//     lands somewhere else in the file.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  exportedNames,
  missingSiblingImports,
  usableCode,
} from "../../mcp/stdio/tests/missing-imports.mjs";

const DIR = path.dirname(fileURLToPath(import.meta.url));

function dashboardModules() {
  return fs
    .readdirSync(DIR)
    .filter((name) => /\.(mjs|js)$/.test(name) && !name.includes(".test."))
    .filter((name) => fs.statSync(path.join(DIR, name)).isFile())
    .map((name) => [`service/new_dashboard/${name}`, fs.readFileSync(path.join(DIR, name), "utf-8")]);
}

function exportsByFile() {
  return new Map(dashboardModules().map(([file, source]) => [file, exportedNames(source)]));
}

test("no dashboard module uses a sibling's export without importing it", () => {
  const known = exportsByFile();
  const offenders = [];
  for (const [file, source] of dashboardModules()) {
    for (const hit of missingSiblingImports(file, source, known)) {
      offenders.push(`${file}: ${hit.name} (exported by ${hit.from})`);
    }
  }
  assert.deepEqual(
    offenders, [],
    "these names are used here and exported by a module this file already imports from, but are "
    + "not imported — the browser throws ReferenceError when the line runs:\n  "
    + offenders.join("\n  "),
  );
});

test("the dashboard population is real", () => {
  // A directory read that silently returned nothing would make the assertion above vacuous — the
  // same anti-vacuity the dead-import gate needed when it was pointed here.
  const modules = dashboardModules();
  assert.ok(modules.length >= 40, `only ${modules.length} dashboard modules found`);
  assert.ok(modules.some(([f]) => f.endsWith("/app.js")), "app.js missing from the scan");
  const total = [...exportsByFile().values()].reduce((n, names) => n + names.size, 0);
  assert.ok(total > 200, `only ${total} exported names found across the dashboard`);
});

test("the detector fires on THIS module set, not just the bridge's", () => {
  // The dead-import gate's lesson, applied before trusting a green run here: a rule can be blind to
  // a whole population and report it clean. So exercise it against dashboard-shaped source —
  // single-quoted specifiers, template-literal markup, an arrow-heavy module.
  const known = new Map([["service/new_dashboard/sib.mjs", new Set(["esc", "messageId"])]]);
  const missing = [
    "import { esc } from './sib.mjs';",
    "export const row = (m) => `<b>${esc(messageId(m))}</b>`;",
  ].join("\n");
  assert.deepEqual(
    missingSiblingImports("service/new_dashboard/m.mjs", missing, known),
    [{ name: "messageId", from: "./sib.mjs" }],
    "a call inside a template interpolation is a real use",
  );

  const fixed = [
    "import { esc, messageId } from './sib.mjs';",
    "export const row = (m) => `<b>${esc(messageId(m))}</b>`;",
  ].join("\n");
  assert.deepEqual(missingSiblingImports("service/new_dashboard/m.mjs", fixed, known), []);
});

test("markup text inside a template is not a use", () => {
  // `static-links.mjs`'s `` `${apiOrigin}/api/v1/dashboard` `` — a URL path segment that reads as an
  // identifier. The dashboard is full of these; the bridge has almost none, which is why the hole
  // only appeared when the rule crossed populations.
  const known = new Map([["service/new_dashboard/sib.mjs", new Set(["api", "esc"])]]);
  const urlish = [
    "import { esc } from './sib.mjs';",
    "export const href = (o) => `${o}/api/v1/dashboard`;",
    "export const cls = () => `<div class=\"api-panel\">x</div>`;",
  ].join("\n");
  assert.deepEqual(missingSiblingImports("service/new_dashboard/m.mjs", urlish, known), []);

  // …and the interpolations inside those same literals are still read.
  assert.match(usableCode("const h = `${o}/api/v1/x`;"), /\bo\b/);
  assert.doesNotMatch(usableCode("const h = `${o}/api/v1/x`;"), /dashboard|v1/);
});

test("a NESTED template's interpolation is still read", () => {
  // THE SHAPE THIS GATE'S OWN DEFECT IS WRITTEN IN, and the one my first fixture was too shallow to
  // cover. `run-inspector.mjs` renders a template inside another template's interpolation, so
  // extracting `${...}` with a brace-free pattern dropped the inner call — and reverting the real
  // fix left the gate GREEN. Mutation caught that; the one-level fixture did not.
  const known = new Map([["service/new_dashboard/sib.mjs", new Set(["esc", "messageId"])]]);
  const nested = [
    "import { esc } from './sib.mjs';",
    "export const row = (m, on) => `<div>${on ? `<b>${esc(messageId(m))}</b>` : ''}</div>`;",
  ].join("\n");
  assert.deepEqual(
    missingSiblingImports("service/new_dashboard/m.mjs", nested, known),
    [{ name: "messageId", from: "./sib.mjs" }],
    "a call two template levels deep is still a use",
  );

  // …and the markup around it at BOTH levels is still text.
  const code = usableCode(nested);
  assert.match(code, /messageId/);
  assert.doesNotMatch(code, /div|<b>/, "markup at either nesting level must not read as code");
});

test("a template continued with a backslash does not mis-pair its delimiters", () => {
  // The second hole this extension found. `static-links.mjs` writes its install snippet across two
  // lines with a trailing backslash; with `\\.` in the delimiter pattern that literal never matched,
  // its opening backtick paired with a later one, and the text in between was exposed as code.
  const continued = [
    "import { esc } from './sib.mjs';",
    "export const snippet = (o) => `bash install.sh --client claude \\",
    "  ${o} --with-hook`;",
    "export const link = (o) => `${o}/api/v1/dashboard`;",
  ].join("\n");
  const known = new Map([["service/new_dashboard/sib.mjs", new Set(["api", "esc"])]]);
  assert.deepEqual(
    missingSiblingImports("service/new_dashboard/m.mjs", continued, known), [],
    "the second template must still be recognised as a literal",
  );
});
