// No parameter default may name something its module does not define.
//
// THE INCIDENT, 2026-08-26. `proc-probes.js` carried
//
//     import { spawnSync } from "child_process";
//     export function defaultListProcesses(spawnSync = nodeSpawnSync) { ... }
//
// since the v0.5.4 extraction `32ce11fa`, which rewrote that import from `spawnSync as nodeSpawnSync`
// and left the signature byte-identical. `nodeSpawnSync` then existed nowhere.
//
// IT WAS INVISIBLE TO EVERY EXISTING INSTRUMENT, which is the argument for this file:
//   - `node --check` parses it; the name resolves at CALL time, not parse time.
//   - The unit suite passes; every test INJECTS a spawn, so the default never evaluates.
//   - `no-missing-sibling-imports` passes; `moduleBindings` counts a default's VALUE as a binding.
//
// AND ITS FAILURE WAS SILENT. A default is evaluated BEFORE the function body, so the ReferenceError
// escapes that function's own try/catch. `enumerateManagedSurvivors` calls it as
// `try { procs = listProcesses() || [] } catch { procs = [] }` -- so the managed-survivor sweep
// enumerated ZERO processes and reaped nothing, on every platform, while reporting success. Seven
// orphaned managed processes were found alive on the operator's host the day this was found.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  definedNames,
  parameterLists,
  undefinedDefaultArguments,
  usesAndBindings,
} from "./default-arguments.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
// The whole bridge, not one named file. A scan of the module that happened to break is a scan that
// proves one line was fixed.
const DIRS = ["mcp/stdio", "mcp/stdio/adapters", "mcp/stdio/controllers", "service/new_dashboard"];

function productSources() {
  const out = [];
  for (const dir of DIRS) {
    let names;
    try {
      names = readdirSync(path.join(REPO, dir));
    } catch {
      continue;
    }
    for (const name of names) {
      const full = path.join(REPO, dir, name);
      if (!statSync(full).isFile()) continue;
      if (!/\.(js|mjs)$/.test(name) || /\.test\./.test(name)) continue;
      out.push([`${dir}/${name}`, readFileSync(full, "utf8")]);
    }
  }
  return out;
}

test("the scan walks a real population", () => {
  // ANTI-VACUITY. An empty file list reports zero offenders and reads exactly like a clean repo.
  const sources = productSources();
  assert.ok(sources.length > 150, `only ${sources.length} product modules walked`);
});

test("the detector can say PRESENT", () => {
  // The incident, reproduced verbatim. A gate that cannot fail on the defect it was built from
  // cannot pass on anything.
  const found = undefinedDefaultArguments([
    'import { spawnSync } from "child_process";',
    "export function defaultListProcesses(spawnSync = nodeSpawnSync) { return spawnSync(); }",
  ].join("\n"));
  assert.deepEqual(found.map((f) => f.value), ["nodeSpawnSync"]);
});

test("the detector can say ABSENT", () => {
  // The SAME module, once the alias exists. Both controls, in the same run as the zero they defend.
  const found = undefinedDefaultArguments([
    'import { spawnSync as nodeSpawnSync } from "node:child_process";',
    "export function defaultListProcesses(spawnSync = nodeSpawnSync) { return spawnSync(); }",
  ].join("\n"));
  assert.deepEqual(found, []);
});

test("a default naming an earlier parameter of the same list is fine", () => {
  // `function f(a, b = a)` is legal and common. Reporting it would be the cry-wolf that gets a gate
  // deleted.
  assert.deepEqual(undefinedDefaultArguments("function f(a, b = a) { return b; }"), []);
});

test("a default naming a local declaration or an ambient global is fine", () => {
  assert.deepEqual(undefinedDefaultArguments("const FALLBACK = 1;\nfunction f(a = FALLBACK) { return a; }"), []);
  assert.deepEqual(undefinedDefaultArguments("function f(env = process) { return env; }"), []);
});

test("a CALL's arguments are not parameter defaults", () => {
  // `f(a = b)` at a call site is an assignment expression, and `b` is an ordinary use the
  // missing-sibling-import gate already covers. Judging it here would double-report and misattribute.
  assert.deepEqual(undefinedDefaultArguments("const x = 1;\nfoo(x = someGlobalThing);"), []);
});

test("comments and strings are not code", () => {
  // The scratch version of this scan reported 11 offenders, every one of them a `foo = bar` written
  // inside a comment. `usableCode` is why this one does not.
  const src = [
    "// a default like status = interrupted is prose, not code",
    'const s = "running = True";',
    "export function f(a = 1) { return a; }",
  ].join("\n");
  assert.deepEqual(undefinedDefaultArguments(src), []);
});

test("the parameter-list scanner separates a use from a binding", () => {
  const { uses, bindings } = usesAndBindings("spawnSync = nodeSpawnSync");
  assert.ok(uses.has("nodeSpawnSync"), "the default's VALUE is a use");
  assert.ok(bindings.has("spawnSync"), "the parameter's NAME is a binding");
  assert.ok(!uses.has("spawnSync"), "a parameter name is never a use");
});

test("a destructured default is judged too", () => {
  // `function f({ a = MISSING } = {})` throws exactly the same way.
  const found = undefinedDefaultArguments("export function f({ a = MISSING } = {}) { return a; }");
  assert.deepEqual(found.map((x) => x.value), ["MISSING"]);
});

test("definedNames sees imports, aliases and declarations", () => {
  const names = definedNames([
    'import def, { a, b as c } from "./x.js";',
    'import * as ns from "./y.js";',
    "const d = 1; let e = 2; function g() {} class H {}",
    "const { i, j: k } = obj;",
  ].join("\n"));
  for (const expected of ["def", "a", "c", "ns", "d", "e", "g", "H", "i", "k"]) {
    assert.ok(names.has(expected), `definedNames missed ${expected}`);
  }
  assert.ok(!names.has("b"), "an alias import binds only the local name");
});

test("parameterLists finds a list and skips a call", () => {
  const lists = parameterLists("function f(a, b) { g(c, d); }");
  assert.deepEqual(lists, ["a, b"]);
});

// THE THREE SHAPES THIS SCAN REPORTED WRONGLY ON ITS FIRST RUN, each pinned so the next edit to the
// detector cannot bring it back. Thirteen offenders were reported and every one was the scan's fault:
// a gate that cries wolf on working code gets deleted, and then the real defect has nothing watching.

test("an operator that contains an equals sign is not an assignment", () => {
  // `counter.count >= grace` was reported as a default naming `grace`. Five of the thirteen were this.
  for (const src of [
    "function f(a) { if (a.count >= grace) return 1; return 0; }",
    "function f(a) { if (a.status !== expected) return 1; return 0; }",
    "function f(a) { let n = 0; n += stepSize; return n; }",
  ]) {
    assert.deepEqual(undefinedDefaultArguments(src), [], src);
  }
});

test("a condition is not a parameter list", () => {
  // `if (attempts >= threshold) {` is followed by `{` exactly as a function header is.
  assert.deepEqual(parameterLists("if (a) { b(); } while (c) { d(); } for (e) { f(); }"), []);
  assert.deepEqual(undefinedDefaultArguments("function f(a) { if (a === someUndeclaredThing) return 1; return 0; }"), []);
});

test("a keyword in a default is not a name to resolve", () => {
  // `= new Foo()`, `= async () => {}` and `= typeof X` each put a reserved word where an identifier
  // would sit. Three more of the thirteen.
  for (const src of [
    ["class Q {}", "function f(a = new Q()) { return a; }"].join("\n"),
    "function f(a = async () => {}) { return a; }",
    "function f(a = typeof self) { return a; }",
  ]) {
    assert.deepEqual(undefinedDefaultArguments(src), [], src);
  }
});

test("no product module has a default naming something undefined", () => {
  const offenders = [];
  for (const [rel, source] of productSources()) {
    for (const found of undefinedDefaultArguments(source)) {
      offenders.push(`${rel}  (${found.param})  -> ${found.value}`);
    }
  }
  assert.deepEqual(offenders, [], (
    "these parameter defaults name something their module does not define, so every call that omits "
    + "the argument throws a ReferenceError BEFORE the function body -- outside its own try/catch:\n  "
    + offenders.join("\n  ")
    + "\nImport it, declare it, or replace the default with a literal."
  ));
});
