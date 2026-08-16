// Every dependency a dashboard module takes from app.js must be VALIDATED before it is bound.
//
// Eight modules use the same late-binding shape: declare a module-level placeholder, export an
// `init*(deps)` that destructures the real thing out of a bag, and refuse a partial bag rather than
// accepting the placeholder. `agent-session-actions.mjs` says it plainly — "Throws on a partial bag
// rather than accepting no-ops."
//
// THE PLACEHOLDER IS WHY THIS MATTERS. An unvalidated dependency does not arrive as `undefined` and
// crash; it stays whatever the module declared — `let setPage = () => {}` — so the feature silently
// does nothing. A nav control that no longer navigates, a console that never resyncs, a
// notification that is never raised: no error, nothing in the console, and the only symptom is a
// click that does not work.
//
// ALL EIGHT ARE CORRECT TODAY — 36 injected names, every one validated. That is exactly the state
// worth pinning: the lists are hand-written, so the next dependency added to a bag is one edit away
// from being unvalidated, and nothing would report it. Found while fork-scanning the dashboard,
// which came back clean; this is what the scan turned up instead.
//
// TWO VALIDATION SHAPES ARE ACCEPTED, because both give the same guarantee: a `REQUIRED` array
// checked with a `missing` filter (used where there are several), and a direct
// `typeof deps?.x !== 'function'` throw (used by `work-loop-actions.mjs`, which takes one). This
// test does not have an opinion about which — only that every injected name uses one of them.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const DEPS_BAG = /\(\{([\s\S]*?)\}\s*=\s*deps\)/;
const REQUIRED_LIST = /const REQUIRED = \[([\s\S]*?)\];/;

function injectionModules() {
  return fs
    .readdirSync(HERE)
    .filter((name) => /\.mjs$/.test(name) && !/\.test\./.test(name))
    .map((name) => [name, fs.readFileSync(path.join(HERE, name), "utf8").replace(/\r\n/g, "\n")])
    .filter(([, src]) => DEPS_BAG.test(src));
}

function contractOf(src) {
  const destructured = [...(src.match(DEPS_BAG)[1].matchAll(/([A-Za-z_$][\w$]*)/g))].map((m) => m[1]);
  const listed = REQUIRED_LIST.test(src)
    ? [...src.match(REQUIRED_LIST)[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
    : [];
  const typeofChecked = [...src.matchAll(/typeof\s+deps\?\.([A-Za-z_$][\w$]*)/g)].map((m) => m[1]);
  const placeholders = new Set([...src.matchAll(/^let\s+([A-Za-z_$][\w$]*)\s*=/gm)].map((m) => m[1]));
  return { destructured, validated: new Set([...listed, ...typeofChecked]), placeholders };
}

test("the scan finds the injection modules it is about", () => {
  // An empty population reports clean exactly like a correct one, and this scan keys on a code shape
  // that a refactor could rename out from under it.
  const modules = injectionModules().map(([name]) => name);
  assert.ok(modules.length >= 8, `only ${modules.length} modules with a deps bag: ${modules}`);
  for (const expected of ["agent-session-actions.mjs", "click-dispatch.mjs", "work-loop-actions.mjs"]) {
    assert.ok(modules.includes(expected), `${expected} is no longer detected as an injection module`);
  }
});

test("every injected dependency is validated before it is bound", () => {
  const offenders = [];
  for (const [name, src] of injectionModules()) {
    const { destructured, validated } = contractOf(src);
    const missing = destructured.filter((dep) => !validated.has(dep));
    if (missing.length) offenders.push(`${name}: ${missing.join(", ")}`);
  }
  assert.deepEqual(offenders, [], (
    "these dependencies are destructured from the deps bag but never checked, so a caller that omits "
    + "one leaves the module's placeholder in place — the feature then silently does nothing, with no "
    + "error anywhere. Add them to REQUIRED, or check them with typeof.\n  " + offenders.join("\n  ")
  ));
});

test("every injected dependency has a module-level placeholder to bind into", () => {
  // The mirror of the check above. A name destructured with no `let` of its own is assigning to
  // something that is not this module's state — an import, or a stray global.
  const offenders = [];
  for (const [name, src] of injectionModules()) {
    const { destructured, placeholders } = contractOf(src);
    const orphans = destructured.filter((dep) => !placeholders.has(dep));
    if (orphans.length) offenders.push(`${name}: ${orphans.join(", ")}`);
  }
  assert.deepEqual(offenders, [], offenders.join("\n  "));
});

test("the validation really is a throw, not a log", () => {
  // A module that noticed a partial bag and carried on would satisfy the checks above while leaving
  // the placeholder in place — which is the exact failure they exist to prevent.
  for (const [name, src] of injectionModules()) {
    const init = src.slice(src.indexOf("export function init"));
    const body = init.slice(0, init.indexOf("\n}\n") + 3);
    assert.match(body, /throw new TypeError/, `${name}'s init does not throw on a partial bag`);
  }
});

test("every init* is actually called by something", () => {
  // THE CALLER SIDE of the same contract. The checks above guarantee a module REFUSES a partial bag;
  // none of them notices a module whose init is never invoked at all — every placeholder in it stays
  // a no-op and the throw that would have caught a bad bag never runs. All eight are wired from
  // app.js today, which is again true by attention: a ninth module is one boot-wiring edit away from
  // exporting an init nobody calls, and the symptom would be a whole feature quietly doing nothing.
  const sources = fs
    .readdirSync(HERE)
    .filter((name) => /\.(mjs|js)$/.test(name) && !/\.test\./.test(name))
    .map((name) => [name, fs.readFileSync(path.join(HERE, name), "utf8")]);

  const exported = sources.flatMap(([file, src]) =>
    [...src.matchAll(/^export function (init[A-Za-z_$][\w$]*)/gm)].map((m) => ({ name: m[1], file })));
  assert.ok(exported.length >= 8, `only ${exported.length} init* exports found — the scan is too narrow`);

  const uncalled = exported.filter(({ name, file }) => {
    const call = new RegExp(String.raw`\b${name}\s*\(`);
    return !sources.some(([other, src]) => other !== file && call.test(src));
  });
  assert.deepEqual(
    uncalled.map((e) => `${e.file}:${e.name}`), [],
    "these init functions are exported but never called, so their modules run entirely on placeholders",
  );
});

test("the detector notices an unvalidated dependency", () => {
  // Anti-vacuity on a fixture: all eight modules pass today, so a clean run proves nothing about
  // whether the comparison works.
  const good = "let a = () => {};\nlet b = () => {};\n"
    + "export function initX(deps) {\n  const REQUIRED = ['a', 'b'];\n"
    + "  const missing = REQUIRED.filter((k) => deps[k] == null);\n"
    + "  if (missing.length) throw new TypeError('x');\n  ({ a, b } = deps);\n}\n";
  const bad = good.replace("const REQUIRED = ['a', 'b'];", "const REQUIRED = ['a'];");

  assert.deepEqual(contractOf(good).destructured, ["a", "b"]);
  assert.deepEqual(
    contractOf(good).destructured.filter((d) => !contractOf(good).validated.has(d)), [],
    "the good fixture must pass",
  );
  assert.deepEqual(
    contractOf(bad).destructured.filter((d) => !contractOf(bad).validated.has(d)), ["b"],
    "dropping a name from REQUIRED must be caught",
  );
});
