// A name a sibling module EXPORTS, used here, and never imported — the crash `node --check` cannot see.
//
// THIS CLASS HAS SHIPPED TWICE. `doctor.js` used `SERVICE_RUNTIME_PATHS` in a spread and did not
// import it, so `aify-comms doctor` — the tool that proves a deploy took — threw
// `ReferenceError: SERVICE_RUNTIME_PATHS is not defined` on its first line of real work. Writing the
// detector for that found a second: `auto-registration.mjs` calls
// `forgetRemoteAgent(agentId, "server marked it intentionally removed")` on the HTTP 410 branch and
// imported only `REMOTE_AGENT_STATE` from the module that exports it.
//
// The second one is the shape that makes this worth a gate. 410 is the TOMBSTONE refusal: an
// operator removes an agent, a lingering bridge auto-re-registers, the service correctly refuses,
// and the bridge is supposed to drop the agent from its cache. Instead that line would have thrown —
// on the exact path whose job is to stop a lingering bridge from resurrecting a deleted agent.
//
// WHY NOTHING CAUGHT EITHER: `node --check` only PARSES, an undefined name is a runtime error; both
// live in branches no test executes; and JavaScript has no equivalent of
// `scripts/undefined_name_sweep.py`, which catches exactly this on the Python side.
//
// SCOPE, because a sound general check needs a real parser and that is a reviewer's call (adding
// acorn/espree as a devDependency was explicitly ruled not-unilateral). This checks ONE decidable
// slice of the problem and says so: a name that (a) is used in this module, (b) is exported by a
// module this file ALREADY imports from, and (c) is neither imported nor declared here. Both real
// defects are in that slice, because both were imports a sweep or an edit removed while the use
// stayed. It cannot find a free variable that no sibling exports — that needs scope analysis.
//
// THE FALSE-POSITIVE SOURCES ARE THE INTERESTING PART, and each is neutralised below rather than
// exempted: re-export lines name without binding, alias imports leave the ORIGINAL name on the line,
// and a module specifier is text that contains identifiers. My first run reported 20; 19 were those
// three artifacts and one was the real defect.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { bridgeSources } from "./bridge-sources.mjs";
// THE DETECTOR IS IMPORTED, NOT DECLARED HERE. It moved to `missing-imports.mjs` for the reason
// `dead-imports.mjs` records: a test file's top-level `test()` calls RUN on import, so anything
// borrowing one of these functions would execute this suite as a side effect — which is exactly
// what happened to me while debugging it, and I got a TAP dump instead of an answer.
import {
  exportedNames,
  missingSiblingImports,
  moduleBindings,
  usableCode,
  usedFromAnySiblingWithoutImport,
} from "./missing-imports.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// ONE OWNER FOR "WHAT IS THE BRIDGE". These used to call `bridgeSources()` and then ADD
// `adapters/` and `controllers/` by hand — a second, hand-listed walk beside the real one. It was
// redundant the moment `bridgeSources()` learned to recurse, and worse than redundant: the hand list
// could not know about `scripts/`, so a whole directory sat outside this gate while looking covered.
// Hand-listed roots are the exact shape that left fifteen Python files ungoverned by the size gate.
function bridgeModules() {
  return bridgeSources().map(([file, source]) => [`mcp/stdio/${file}`, source]);
}

function bridgeExportsByFile() {
  return new Map(bridgeModules().map(([file, source]) => [file, exportedNames(source)]));
}

test("no bridge module uses a sibling's export without importing it", () => {
  const exportsByFile = bridgeExportsByFile();
  const offenders = [];
  for (const [file, source] of bridgeModules()) {
    for (const hit of missingSiblingImports(file, source, exportsByFile)) {
      offenders.push(`${file}: ${hit.name} (exported by ${hit.from})`);
    }
  }
  assert.deepEqual(
    offenders, [],
    "these names are used here and exported by a module this file already imports from, but are "
    + "not imported — `node --check` parses them fine and they throw ReferenceError when the line "
    + "runs:\n  " + offenders.join("\n  "),
  );
});

test("the population is real — the scan reaches the whole bridge", () => {
  const modules = bridgeModules();
  assert.ok(modules.length > 80, `only ${modules.length} bridge modules scanned`);
  const exportsByFile = bridgeExportsByFile();
  const total = [...exportsByFile.values()].reduce((n, s) => n + s.size, 0);
  assert.ok(total > 200, `only ${total} exported names found across the bridge`);
  assert.ok(
    modules.some(([f]) => f.startsWith("mcp/stdio/adapters/")),
    "the adapters subdirectory must be scanned — the 1000-line gate's Python half missed a whole "
    + "directory the same way",
  );
});

test("the detector fires on the two shapes that actually shipped", () => {
  const exportsByFile = new Map([["mcp/stdio/sib.js", new Set(["WANTED", "helper"])]]);

  // 1. doctor.js: used in a SPREAD, import removed by the dead-import sweep.
  const spread = 'import { other } from "./sib.js";\nconst args = [...WANTED, other];\n';
  assert.deepEqual(
    missingSiblingImports("mcp/stdio/m.js", spread, exportsByFile),
    [{ name: "WANTED", from: "./sib.js" }],
  );

  // 2. auto-registration.mjs: a plain CALL on a branch no test runs.
  const call = 'import { other } from "./sib.js";\nfunction f() { helper(1); return other; }\n';
  assert.deepEqual(
    missingSiblingImports("mcp/stdio/m.js", call, exportsByFile),
    [{ name: "helper", from: "./sib.js" }],
  );

  // …and the fixed forms report nothing.
  const fixed = 'import { WANTED, helper, other } from "./sib.js";\nconst a = [...WANTED, helper(other)];\n';
  assert.deepEqual(missingSiblingImports("mcp/stdio/m.js", fixed, exportsByFile), []);
});

test("the three false-positive sources are neutralised, not exempted", () => {
  const exportsByFile = new Map([["mcp/stdio/sib.js", new Set(["api", "collectOnce", "helper"])]]);

  // RE-EXPORT: names without binding. A hub module is nothing but these lines.
  const reexport = 'import { x } from "./sib.js";\nexport { helper } from "./sib.js";\n';
  assert.deepEqual(missingSiblingImports("mcp/stdio/m.js", reexport, exportsByFile), []);

  // ALIAS: `import { collectOnce as collectUsageOnce }` binds only the alias, but the original is
  // still on the line — 13 of my first 20 hits were this.
  const alias = 'import { collectOnce as collectUsageOnce } from "./sib.js";\nconst y = collectUsageOnce();\n';
  assert.deepEqual(missingSiblingImports("mcp/stdio/m.js", alias, exportsByFile), []);

  // SPECIFIER TEXT: `'./api-client.mjs'` contains `api`.
  const specifier = 'import { x } from "./sib.js";\nimport { z } from "./api-client.mjs";\nconst y = x + z;\n';
  assert.deepEqual(missingSiblingImports("mcp/stdio/m.js", specifier, exportsByFile), []);

  // A LOCAL DECLARATION shadowing a sibling export is not a missing import either.
  const shadow = 'import { x } from "./sib.js";\nfunction helper() { return x; }\nconst y = helper();\n';
  assert.deepEqual(missingSiblingImports("mcp/stdio/m.js", shadow, exportsByFile), []);
});

test("what this CANNOT see is stated, so the gate is not read as more than it is", () => {
  // A free variable that no imported sibling exports is invisible here — that needs real scope
  // analysis, and adding a parser dependency is a reviewer's decision, not this test's.
  const exportsByFile = new Map([["mcp/stdio/sib.js", new Set(["known"])]]);
  const undetectable = 'import { known } from "./sib.js";\nfunction f() { return totallyUndefined(known); }\n';
  assert.deepEqual(
    missingSiblingImports("mcp/stdio/m.js", undetectable, exportsByFile), [],
    "documented limit: only names a SIBLING exports are checked",
  );
});

test("a `/*` inside a LINE comment must not swallow the code after it", () => {
  // THE BUG THAT HID THE ORIGINAL DEFECT FROM THIS VERY GATE. `strip()` removed block comments
  // FIRST, so a glob written in prose — `doctor-predicates.js` says "an AST scan of non-test
  // `service/**`" in a `//` comment — opened a phantom block-comment span running 2,023 characters,
  // which ate the real `export const SERVICE_RUNTIME_PATHS` below it. The detector therefore could
  // not see the export whose missing import crashed `aify-comms doctor`, and my first mutation run
  // reported this gate as NOT catching its own founding defect.
  //
  // Two bridge modules are affected by the order today (`claude-turn-end-detector.js` loses 1,198
  // characters, `doctor-predicates.js` 200), so it is a live property rather than a fixture worry.
  const prose = [
    "export const KEEP = 1;",
    "// a scan of `service/**` and other globs",
    "export const AFTER = 2;",
  ].join("\n");
  const names = exportedNames(prose);
  assert.ok(names.has("KEEP"), "the export before the prose glob survives either way");
  assert.ok(
    names.has("AFTER"),
    "an export AFTER a `/*` written inside a line comment must still be visible — strip line "
    + "comments FIRST, or a glob in prose disables analysis for the rest of the file",
  );

  // A real block comment must still be removed, or the fix trades one blindness for another.
  const blocked = ["export const KEEP = 1;", "/* export const HIDDEN = 2; */"].join("\n");
  assert.ok(exportedNames(blocked).has("KEEP"));
  assert.ok(!exportedNames(blocked).has("HIDDEN"), "a genuine block comment is still stripped");

  // And the live file the incident came from.
  const predicates = fs.readFileSync(path.join(STDIO, "doctor-predicates.js"), "utf-8");
  assert.ok(
    exportedNames(predicates).has("SERVICE_RUNTIME_PATHS"),
    "doctor-predicates.js exports SERVICE_RUNTIME_PATHS — if this fails, the phantom span is back",
  );
});

test("the detector's own helpers behave as the gate assumes", () => {
  // `usableCode` and `moduleBindings` decide every verdict above, so they are exercised directly:
  // a failure then localises to the rule rather than to a whole-bridge scan.
  assert.doesNotMatch(usableCode('export { A } from "./x.js";'), /\bA\b/, "a re-export is not a use");
  assert.match(usableCode("const y = [...SPREAD];"), /\bSPREAD\b/, "a spread IS a use");

  const { bound, specifiers } = moduleBindings(
    ['import { a as b } from "./x.js";', "const c = 1;"].join("\n"),
  );
  assert.ok(bound.has("b"), "the alias is bound");
  assert.ok(!bound.has("a"), "the ORIGINAL name is not bound by an alias import");
  assert.ok(bound.has("c"), "a local declaration is bound");
  assert.ok(specifiers.has("./x.js"));
});

// ── ANY sibling, not only one the module already imports from (v0.7.2, external review) ──────────
// The rule above cannot see a module whose WHOLE import line for a name was deleted: it imports
// nothing from that sibling any more. `moved-names-resolve.test.js` covered a narrower case until
// 181c45c4 removed it. The dashboard's wider detector, now shared, asks the question for the bridge.

test("no bridge module uses ANY sibling's export without importing it", () => {
  const known = bridgeExportsByFile();
  const offenders = [];
  for (const [file, source] of bridgeModules()) {
    for (const hit of usedFromAnySiblingWithoutImport(file, source, known)) {
      offenders.push(`${file}: ${hit.name} (exported by ${hit.from})`);
    }
  }
  assert.deepEqual(offenders, [],
    "these names are used here, exported by a sibling this module does not import them from, and not "
    + "declared here -- they throw ReferenceError when the line runs:\n  " + offenders.join("\n  "));
});

test("the wider scan finds a deleted import line, and not a parameter with nested parentheses", () => {
  const known = new Map([["mcp/stdio/ports.mjs", new Set(["listListeners"])]]);
  const at = (src) => usedFromAnySiblingWithoutImport("mcp/stdio/m.mjs", src, known).map((h) => h.name);
  // The investigator's mutation: the sole import of a name removed, its use left behind.
  assert.deepEqual(at("export function f() {\n  return listListeners();\n}\n"), ["listListeners"]);
  assert.deepEqual(at('import { listListeners } from "./ports.mjs";\nexport const x = listListeners();\n'), []);
  // A destructured parameter after a default holding parentheses (gateway-orphan-check.mjs).
  assert.deepEqual(at("export function f({ a, imageOf = () => null, listListeners }) {\n  return listListeners();\n}\n"), []);
  // A use INSIDE a parameter's default is still a use: hermes-prior-reap.mjs's `listeners = () =>
  // listListeners(...)` with its import deleted is the mutation this gate was checked against.
  assert.deepEqual(at("export function f({ listeners = () => listListeners({}) } = {}) {\n  return listeners();\n}\n"), ["listListeners"]);
  // comms-senior-dev's probe of v0.7.2: a default with NO nested parentheses, which the shared
  // parameter pattern in `moduleBindings` matches and read whole, so `listListeners` counted as bound
  // and both rules stayed silent while `f()` throws ReferenceError.
  const probe = 'import { other } from "./sib.js";\nexport function f({listeners = listListeners} = {}) { return listeners + other; }\n';
  const sib = new Map([["mcp/stdio/sib.js", new Set(["other", "listListeners"])]]);
  assert.equal(moduleBindings(probe).bound.has("listListeners"), false, "a default's value was read as a bound name");
  assert.deepEqual(missingSiblingImports("mcp/stdio/m.mjs", probe, sib).map((h) => h.name), ["listListeners"]);
  assert.deepEqual(usedFromAnySiblingWithoutImport("mcp/stdio/m.mjs", probe, sib).map((h) => h.name), ["listListeners"]);
  assert.deepEqual(at("export const g = ({ listeners = listListeners } = {}) => listeners;\n"), ["listListeners"], "the arrow form too");
  assert.ok(moduleBindings(probe).bound.has("listeners"), "CONTROL: the parameter itself is bound");
  // A destructuring whose default holds a block (hermes-active-session.mjs).
  assert.deepEqual(at("const {\n  nextId = (() => { let n = 1; return () => n++; })(),\n  listListeners = 1,\n} = opts;\nlistListeners;\n"), []);
});
