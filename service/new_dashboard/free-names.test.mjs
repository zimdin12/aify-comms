// The free-name sweep, and the sweep applied to every dashboard module.
//
// Two halves. The first pins the detector against cases it must and must not report — including the
// template-interpolation case that let four missing imports ship. The second runs it over every
// module in this directory, which is the part that catches the next one.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { exportedNames, freeNames, missingImports } from "./free-names.mjs";

const dir = path.dirname(fileURLToPath(import.meta.url));

test("a name used but never declared is reported", () => {
  assert.deepEqual(freeNames("export function f() { return missingHelper(1); }"), ["missingHelper"]);
});

test("an IMPORTED name is not reported", () => {
  assert.deepEqual(freeNames("import { helper } from './x.mjs';\nexport function f() { return helper(1); }"), []);
});

test("a RENAMED import is not reported under either name", () => {
  const src = "import { helper as h } from './x.mjs';\nexport function f() { return h(1); }";
  assert.deepEqual(freeNames(src), []);
});

test("A NAME USED ONLY INSIDE A TEMPLATE INTERPOLATION IS REPORTED", () => {
  // The exact miss. Blanking whole template literals — the obvious way to stop matching words in HTML
  // markup — erases this reference and reports the module clean, which is what happened.
  assert.deepEqual(freeNames("export function f(x) { return `count: ${pendingCount(x)}`; }"), ["pendingCount"]);
});

test("words in template MARKUP are not reported", () => {
  // The other direction, and why blanking was tempting in the first place. `div`, `span` and `section`
  // are not references; a detector that flagged them would be unusable on a render module.
  const src = "export function f(x) { return `<div class='a'><span>${x}</span></div>`; }";
  assert.deepEqual(freeNames(src), []);
});

test("words in comments and strings are not reported", () => {
  const src = [
    "// mentions someHelper in prose",
    "/* and someOtherHelper in a block */",
    "export const s = 'yetAnotherHelper()';",
    'export const t = "andThisOne.thing";',
  ].join("\n");
  assert.deepEqual(freeNames(src), []);
});

test("locally declared names — const, function, class, catch, destructuring — are not reported", () => {
  const src = [
    "import { api } from './x.mjs';",
    "const { a, b: renamed } = api();",
    "let c = 1;",
    "function d() { return 1; }",
    "class E {}",
    "export function f([g, h], { i = 2, ...j } = {}) {",
    "  try { d(); } catch (err) { return err; }",
    "  for (const k of [a, renamed, c, i, j, g, h]) if (k) return new E();",
    "  return null;",
    "}",
  ].join("\n");
  assert.deepEqual(freeNames(src), []);
});

test("browser and standard globals are not reported", () => {
  const src = "export function f() { document.getElementById('x'); setTimeout(() => WebSocket.OPEN, 1); return JSON.stringify(new Date()); }";
  assert.deepEqual(freeNames(src), []);
});

test("missingImports reports a name a SIBLING EXPORTS and this module forgot to import", () => {
  const index = exportedNames({ "helpers.mjs": "export function relTime(t) { return t; }" });
  assert.deepEqual(
    missingImports("export function f(t) { return `at ${relTime(t)}`; }", index),
    [{ name: "relTime", from: ["helpers.mjs"] }],
  );
});

test("missingImports stays quiet once the import is there", () => {
  const index = exportedNames({ "helpers.mjs": "export function relTime(t) { return t; }" });
  const src = [
    "import { relTime } from './helpers.mjs';",
    "export function f(t) { return relTime(t); }",
  ].join("\n");
  assert.deepEqual(missingImports(src, index), []);
});

test("missingImports ignores free names NOTHING exports — the heuristic's noise floor", () => {
  // `freeNames` reports words it cannot resolve: a sentence ending in "only.", the `thread` in a
  // `data-thread-id` attribute, the second declarator of `const first = a, last = b;`. None of them is
  // exported by anything, so intersecting with the sibling export set removes them WITHOUT an
  // allowlist that would rot. Asserted here so the noise floor is a property, not a coincidence.
  const index = exportedNames({ "helpers.mjs": "export function relTime(t) { return t; }" });
  const noisy = "export function f() { return somethingNobodyExports(1); }";
  assert.deepEqual(freeNames(noisy), ["somethingNobodyExports"], "the raw heuristic reports it — that is the premise");
  assert.deepEqual(missingImports(noisy, index), [], "…and the sharpened one does not");
});

test("A PLAIN VALUE REFERENCE IS FOUND — the case call-position filtering hid", () => {
  // `${apiBase}/agents/...` is neither a call nor a member access, so the readable default output
  // skips it. `apiBase` shipped missing from a module for exactly that reason. `missingImports` asks
  // for the unfiltered set instead; the sibling-export intersection is the stronger filter anyway.
  const src = "export function f(id) { return `${apiBase}/agents/${id}`; }";
  const index = exportedNames({ "api-client.mjs": "export let apiBase = '';" });
  assert.deepEqual(freeNames(src), [], "the default view genuinely does not report it");
  assert.deepEqual(missingImports(src, index), [{ name: "apiBase", from: ["api-client.mjs"] }]);
});

test("a template with a BACKSLASH LINE CONTINUATION is still blanked", () => {
  // `\.` does not match a newline, so the template regex failed to match this at all and its text
  // leaked into the scan — reporting `api` from the `/api/v1` inside a URL.
  const src = [
    "import { apiOrigin } from './api-client.mjs';",
    "export function f() {",
    "  return `bash install.sh --client claude \\",
    "  ${apiOrigin}/api/v1 --with-hook`;",
    "}",
  ].join("\n");
  const index = exportedNames({ "api-client.mjs": ["export let api = 1;", "export let apiOrigin = '';"].join("\n") });
  assert.deepEqual(missingImports(src, index), []);
});

test("exportedNames picks up both declaration exports and export-lists", () => {
  const index = exportedNames({
    "a.mjs": ["export const x = 1;", "export async function y() {}", "export class Z {}"].join("\n"),
    "b.mjs": ["const q = 1;", "export { q, q as aliased };"].join("\n"),
  });
  assert.deepEqual([...index.keys()].sort(), ["aliased", "q", "x", "y", "Z"].sort());
  assert.deepEqual(index.get("q"), ["b.mjs"]);
});

test("NO DASHBOARD MODULE USES A SIBLING'S EXPORT WITHOUT IMPORTING IT", () => {
  // The gate this file exists for. A relocation moves code and leaves its import behind; the result
  // parses, reconstructs byte-identically, and passes every suite, because the missing name is only
  // reached on a path no unit test walks. In a browser it is a ReferenceError the first time an
  // operator clicks the control.
  //
  // app.js is excluded: it is the orchestrator that everything is extracted OUT of, it is imported by
  // nothing, and it legitimately declares names its siblings also export. Tests and fixtures are
  // excluded because their fakes and frozen text are not shipped.
  const skip = (name) => name === "app.js" || name.includes(".test.");
  const modules = fs.readdirSync(dir)
    .filter((name) => (name.endsWith(".mjs") || name.endsWith(".js")) && !skip(name))
    .sort();
  assert.ok(modules.length > 30, `expected the module set, found ${modules.length}`);

  const sources = Object.fromEntries(modules.map((name) => [name, fs.readFileSync(path.join(dir, name), "utf8")]));
  const index = exportedNames(sources);
  assert.ok(index.size > 100, `the export index must be populated, found ${index.size}`);

  const offenders = [];
  for (const [name, src] of Object.entries(sources)) {
    for (const finding of missingImports(src, index)) {
      if (finding.from.includes(name)) continue; // it exports the name itself
      offenders.push(`${name} uses ${finding.name} (exported by ${finding.from.join(", ")}) without importing it`);
    }
  }
  assert.deepEqual(offenders, [], offenders.join("; "));
});
