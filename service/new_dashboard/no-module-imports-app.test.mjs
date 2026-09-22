// No dashboard module imports app.js.
//
// WHY. app.js is the render orchestrator. Its module scope runs browser code (it throws
// `ReferenceError: location is not defined` under Node), and every extracted module that needs one of
// its names -- `refresh`, `renderAll`, `evaluateFlowGates`, `resyncActiveConsole` -- takes it INJECTED
// instead, so the module stays importable and testable. An `import ... from "./app.js"` anywhere undoes
// that for the importer and for everything that imports the importer.
//
// WHAT THIS REPLACED. Four tests each carried a hand-typed list of the names ONE module must not import
// (refresh-cycle, run-inspector, session-console, xterm-mount). A list covers the names somebody
// remembered and the modules somebody wrote a test for; a module extracted next week had no list at all.
// This derives the population instead -- every non-test .js/.mjs file in this directory -- and asks the
// question the lists stood in for.
//
// WHAT IT DOES NOT COVER, said so nobody assumes it: a module importing one of those names from a
// SIBLING that does not import app.js is allowed here. That drags no app.js code in, which is the
// property; the old lists also forbade it, by name.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(DIR, "app.js");

/** Every module specifier a source imports or re-exports from: static, bare side-effect, and dynamic. */
export function importSpecifiers(source) {
  const specs = [];
  const patterns = [
    /\b(?:import|export)\b[^"'`;]*?\bfrom\s*["']([^"']+)["']/g,
    /^\s*import\s*["']([^"']+)["']/gm,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const re of patterns) for (const m of source.matchAll(re)) specs.push(m[1]);
  return specs;
}

/** The files in `modules` ([name, source] pairs, relative to `dir`) that import `target`. A relative
 *  specifier resolves from the IMPORTING file's folder, as the module loader does. */
export function importersOf(modules, dir, target) {
  return modules
    .filter(([name, source]) =>
      importSpecifiers(source).some((spec) =>
        spec.startsWith(".") && path.resolve(path.dirname(path.join(dir, name)), spec) === target))
    .map(([name]) => name);
}

function dashboardModules() {
  // RECURSIVE, so a module moved into a subdirectory stays inside the population. A plain
  // `readdirSync` stopped at the top level, which meant the first person to group modules into a
  // folder would silently take them out of this gate's reach (external review, finding 12).
  //
  // `vendor/` is third-party code, not ours to hold to this. `fixtures/` stays in: its harnesses
  // import dashboard modules through `../` and one runs under Node, where an app.js import throws.
  return fs
    .readdirSync(DIR, { recursive: true })
    .map((name) => String(name).split("\\").join("/"))
    .filter((name) => /\.(mjs|js)$/.test(name) && !name.includes(".test.") && name !== "app.js")
    .filter((name) => !name.startsWith("vendor/"))
    .filter((name) => fs.statSync(path.join(DIR, name)).isFile())
    .map((name) => [name, fs.readFileSync(path.join(DIR, name), "utf-8")]);
}

test("no dashboard module imports app.js", () => {
  assert.deepEqual(importersOf(dashboardModules(), DIR, APP), [],
    "app.js names reach a module by injection; an import runs app.js's browser-only module scope");
});

test("NEGATIVE CONTROL: every import form of app.js is reported", () => {
  for (const source of [
    'import { refresh } from "./app.js";',
    "import {\n  renderAll,\n  refresh,\n} from './app.js';",
    'import "./app.js";',
    'const app = await import("./app.js");',
    'export { refresh } from "./app.js";',
    'import { x } from "../new_dashboard/app.js";',
  ]) {
    assert.deepEqual(importersOf([["m.mjs", source]], DIR, APP), ["m.mjs"], `missed: ${source}`);
  }
  // A specifier resolves from its own file's folder, not the dashboard root.
  assert.deepEqual(importersOf([["sub/m.mjs", 'import { refresh } from "../app.js";']], DIR, APP), ["sub/m.mjs"],
    "a subfolder's ../app.js is app.js");
  // …and mentioning app.js is not importing it, which is how every extracted module's header reads.
  for (const source of [
    "// Extracted from app.js in v0.5.4; app.js imports it back.",
    'import { state } from "./state.mjs";',
    'import { refresh } from "./app.mjs";',
  ]) {
    assert.deepEqual(importersOf([["m.mjs", source]], DIR, APP), [], `false alarm: ${source}`);
  }
  assert.deepEqual(importersOf([["sub/m.mjs", 'import { x } from "./app.js";']], DIR, APP), [],
    "a subfolder's ./app.js is its own sibling, not the orchestrator");
});

test("POSITIVE CONTROL: the scan sees real imports in the real population", () => {
  // An empty answer above means nothing if the directory read or the extractor returned nothing.
  const modules = dashboardModules();
  assert.ok(modules.length >= 60, `only ${modules.length} dashboard modules found`);
  assert.ok(modules.some(([name]) => name === "refresh-cycle.mjs"), "a known module is missing from the scan");
  // A known edge, found by the same function the gate uses: session-console.mjs imports state.mjs.
  assert.deepEqual(
    importersOf(modules, DIR, path.join(DIR, "state.mjs")).includes("session-console.mjs"), true,
    "the extractor must find session-console.mjs importing state.mjs",
  );
  const total = modules.reduce((n, [, source]) => n + importSpecifiers(source).length, 0);
  assert.ok(total > 100, `only ${total} import specifiers found across the dashboard`);
});
