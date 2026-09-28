// No dashboard module reads a name it never declares, imports or receives: the ReferenceError that parses.
//
// EXTERNAL REVIEW, 2026-09-29: "A bad import is caught now, but an undefined call still passes every test."
// `no-missing-sibling-imports` finds a name a sibling EXPORTS and this file forgot to import. It cannot see a
// name no sibling exports, and that was the live one: `openRunConsole` in `console-actions.mjs` called
// `renderSessionWorkspace()`, which is module-scoped in app.js and was never injected. Every Open console
// jump from a run threw ReferenceError after navigating, so the inspector never closed. This gate found it
// and nothing else on its first run.
//
// THE PARSER is the acorn Node bundles (`--expose-internals`), so the scan runs in a child. It is loaded
// or the gate FAILS: a gate that skips when it cannot parse passes on every host that lacks the parser.
// `undeclared-names.mjs` says which slice it decides; the controls below pin both edges of it.
//
// THE KNOWN NAMES are JavaScript's own globals, read from a fresh V8 context rather than listed, plus the
// browser APIs the dashboard reads. Those cannot be derived in Node, so they are listed, and the last test
// fails on any entry no module reads: the list can only hold what is in use.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** The browser globals a dashboard module reads bare. */
const ALLOWED_BROWSER = [
  "document", "window", "location", "navigator", "localStorage", "fetch", "WebSocket",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame", "getComputedStyle",
  "innerHeight", "innerWidth", "atob", "URL", "URLSearchParams", "FormData", "Event", "AbortController",
  "ResizeObserver", "Notification",
];

// Runs in the child: load Node's acorn, derive the JS globals, scan each { file, source } read from stdin.
const CHILD = `
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { undeclaredNames } from ${JSON.stringify(pathToFileURL(path.join(HERE, "undeclared-names.mjs")).href)};
const require = createRequire(${JSON.stringify(pathToFileURL(path.join(HERE, "/")).href)});
const acorn = require('internal/deps/acorn/acorn/dist/acorn');
const walk = require('internal/deps/acorn/acorn-walk/dist/walk');
const { sources, browser } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const known = new Set([...vm.runInContext('Object.getOwnPropertyNames(globalThis)', vm.createContext()), ...browser]);
const hits = sources.map(({ file, source }) => ({ file, hits: undeclaredNames(source, { parse: acorn.parse, walk, known }) }));
process.stdout.write(JSON.stringify({ acorn: acorn.version, hits }));
`;

/** Scan sources with the real parser. Throws, failing the test, if the parser cannot be loaded. */
function scan(sources, browser = ALLOWED_BROWSER) {
  const result = spawnSync(process.execPath, ["--expose-internals", "--input-type=module", "-e", CHILD], {
    input: JSON.stringify({ sources, browser }), encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
  });
  assert.equal(result.status, 0, `the scan could not run, so nothing was checked:\n${result.stderr}`);
  return JSON.parse(result.stdout);
}
const namesIn = (source) => scan([{ file: "x.mjs", source }]).hits[0].hits.map((h) => h.name);

function dashboardModules() {
  return fs.readdirSync(HERE)
    .filter((name) => /\.m?js$/.test(name) && !/\.test\./.test(name))
    .map((file) => ({ file, source: fs.readFileSync(path.join(HERE, file), "utf8") }));
}

test("the scan runs on Node's own acorn", () => {
  assert.match(scan([]).acorn, /^\d+\.\d+/, "no acorn version came back");
});

test("CONTROL: an undeclared call is found, and every way of declaring a name is honoured", () => {
  const source = [
    "import { imported } from './a.mjs';",
    "import fallback, * as ns from './b.mjs';",
    "const { left, right: [inner = seed], ...rest } = imported;",
    "export function run(param, { field }, [slot], ...tail) {",
    "  try { imported(param, field, slot, tail, left, inner, rest, ns, fallback, arguments); } catch (err) { return err; }",
    "  class Local {}",
    "  return new Local() && helper() && renderSessionWorkspace();",
    "}",
  ].join("\n");
  assert.deepEqual(namesIn(source), ["seed", "helper", "renderSessionWorkspace"]);
});

test("CONTROL: text that only LOOKS like a name is not read, and writes and interpolations are", () => {
  const source = [
    "const obj = { ghostKey: 1, 'ghostString': 2 };",
    "obj.ghostMember = `${spliced}`;",
    "label: for (;;) break label;",
    "shorthand = { absent };",
    "for (loopVar in obj) {}",
    "export { obj as ghostExport };",
  ].join("\n");
  assert.deepEqual(namesIn(source), ["spliced", "shorthand", "absent", "loopVar"]);
});

test("the slice it cannot decide: a name bound in ANOTHER scope reads as declared", () => {
  // Stated as a pinned limit rather than left for a green run to imply more. Scope analysis would catch
  // this; the flat rule does not, and it is not what an extraction leaves behind.
  assert.deepEqual(namesIn("function a() { const only = 1; }\nexport function b() { return only; }"), []);
});

test("no dashboard module reads a name it never declares, imports or is given", () => {
  const modules = dashboardModules();
  // Population controls: an empty or misnamed population reports clean exactly like a correct one.
  assert.ok(modules.length >= 60, `only ${modules.length} dashboard modules found`);
  for (const expected of ["app.js", "console-actions.mjs", "ui.js"]) {
    assert.ok(modules.some((m) => m.file === expected), `${expected} is not in the scanned population`);
  }
  const offenders = scan(modules).hits.flatMap(({ file, hits }) => hits.map((h) => `${file}:${h.line} ${h.name}`));
  assert.deepEqual(offenders, [], `names read with no declaration, import or injection:\n  ${offenders.join("\n  ")}`);
});

test("every browser name the gate allows is read by some module", () => {
  // With no browser names allowed, each allowed one must show up as a read: an entry nothing reads is a
  // name the gate would wave through for no reason, and would hide a lost local of the same name.
  const read = new Set(scan(dashboardModules(), []).hits.flatMap(({ hits }) => hits.map((h) => h.name)));
  const unused = ALLOWED_BROWSER.filter((name) => !read.has(name));
  assert.deepEqual(unused, [], `allowed but read by no dashboard module: ${unused.join(", ")}`);
});
