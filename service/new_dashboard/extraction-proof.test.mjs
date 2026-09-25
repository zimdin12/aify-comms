// The parser helpers other gates use: `declarationSpan`/`functionSpan` (a JS declaration by name, measured
// with this repo's own parser) and `moduleScopeBrowserRefs` (browser globals read at import time).
//
// THE APP.JS RECONSTRUCTION PROOF THAT USED TO LIVE HERE IS RETIRED (v0.7). It rebuilt the pre-v0.5.4
// app.js from the current one plus every module extracted since and required byte-identity, so every
// later edit to a moved declaration or to app.js itself had to be declared in a 4,800-line plan. The
// v0.5.x series it guarded closed on 2026-08-17, and the Python analogue was retired on the operator's
// word on 2026-09-18 for the same two reasons: it said nothing about correctness, and it taxed every
// behaviour change. `git log -S` answers where a declaration went.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";

import { declarationSpan, functionSpan, moduleScopeBrowserRefs } from "./extraction-proof.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const LF = String.fromCharCode(10);

test("declarationSpan ends a declaration that carries a TRAILING COMMENT", () => {
  // The terminator test was `line.endsWith(";")`, so `const x = new Map(); // note` never ended and the
  // span ran past the declaration — off the end of the module, for a const declared last, returning null.
  // Found when codex-console.mjs failed to reconstruct with "codexConsoleConnections not found".
  assert.equal(declarationSpan("const a = new Map(); // note", "a").text, "const a = new Map(); // note");

  // The obvious fix — split on the first `//` — is wrong, and this is the case that proves it: the code
  // part of a URL string would end at `'http:` and the span would run on again, one silent failure traded
  // for another. Quote state is tracked instead.
  const url = `const u = "http://example.test/x"; // note`;
  assert.equal(declarationSpan(url, "u").text, url);

  // The balance rule still governs: an IIFE's inner `;` must not terminate the declaration early.
  const iife = ["const w = (() => {", "  const raw = 1;", "  return raw;", "})();"].join(LF);
  assert.equal(declarationSpan(iife, "w").text, iife);
});

test("the browser-globals check separates LOAD-TIME access from a deferred function body", () => {
  // Added when `byId` moved to ui.js in v0.5.4. The check flagged
  // `const byId = (id) => document.getElementById(id);` — a braceless arrow, so the brace-depth counter
  // never saw a body and read `document` as module-scope code. It is not: the module imports fine in Node
  // and only touches the DOM when called.
  //
  // That mattered beyond a false alarm. The only way to satisfy the old check was to reword the
  // declaration into a braced function — which would have broken the byte-identity the reconstruction
  // proof requires of every moved body. A wrong check would have forced a wrong edit.
  assert.deepEqual(moduleScopeBrowserRefs("const byId = (id) => document.getElementById(id);"), [],
    "a braceless arrow body is deferred code, not module-scope access");
  assert.deepEqual(moduleScopeBrowserRefs("export const go = (u) => window.open(u);"), [],
    "…including when exported");

  assert.equal(moduleScopeBrowserRefs("const w = document.title;").length, 1,
    "a real load-time read must still be caught");
  assert.equal(moduleScopeBrowserRefs("const p = document.body.x || ((y) => y);").length, 1,
    "…and must not be excused by an arrow appearing LATER on the same line");
  assert.equal(moduleScopeBrowserRefs(["const f = () => {", "document.title = 1;", "};"].join(LF)).length, 0,
    "a braced body was already excluded by the depth counter; that behaviour is unchanged");

  // A MODULE SPECIFIER IS A PATH, NOT A DEREFERENCE. `message-history.mjs` contains the browser
  // global `history`, so importing it was reported as module-scope browser code -- a verdict the
  // importing module could do nothing about.
  assert.deepEqual(moduleScopeBrowserRefs("import { RECENT_PAGE_LIMIT } from './message-history.mjs';"), [],
    "an import PATH containing a global's name is not a reference to it");
  assert.deepEqual(moduleScopeBrowserRefs("export { x } from './window-utils.mjs';"), [], "…re-exports too");
  assert.deepEqual(moduleScopeBrowserRefs("import './document-styles.mjs';"), [], "…and a side-effect import");

  // AND THE EXEMPTION IS THE PATH, NOT THE LINE. Two statements on one line are legal, and an import
  // must not become a place to hide a load-time dereference.
  assert.equal(moduleScopeBrowserRefs("import x from './a.mjs'; history.back();").length, 1,
    "code after an import on the same line is still module-scope browser access");
  assert.equal(moduleScopeBrowserRefs("import { document } from './a.mjs';").length, 1,
    "a BINDING named after a global is still flagged; only the quoted path is excused");
});

test("the browser-globals check honours a typeof guard, but only for the global it guards", () => {
  // Added when notifications.mjs moved. It opens with
  //   export let notificationsEnabled = readEnabled(typeof localStorage !== 'undefined' ? localStorage : null);
  // which is NOT module-scope browser code: `typeof` is the one reference that never throws on an
  // undeclared name, and the bare use sits in a branch that only evaluates when the global exists. The
  // module imports cleanly in Node — verified before this check runs — so flagging it called an importable
  // module unimportable.
  assert.deepEqual(moduleScopeBrowserRefs("const a = g(typeof localStorage !== 'undefined' ? localStorage : null);"), []);
  assert.deepEqual(moduleScopeBrowserRefs("const d = typeof window === 'undefined' ? null : window.x;"), [],
    "the inverted form of the same guard counts too");

  assert.equal(moduleScopeBrowserRefs("const b = localStorage.getItem(1);").length, 1,
    "an unguarded load-time read must still be caught");

  // THE CASE THAT KEEPS THIS HONEST: guarding one global must not excuse dereferencing another. Without
  // this the exemption would degrade into "any line containing the word typeof passes".
  const mixed = moduleScopeBrowserRefs("const c = typeof localStorage !== 'undefined' ? document.title : null;");
  assert.equal(mixed.length, 1);
  assert.equal(mixed[0].global, "document");
});
test("every dashboard module except app.js IMPORTS in Node", async () => {
  // The property the old per-extraction purity list stood for, asked directly and over a DERIVED set:
  // a module that touches the browser at load is as untestable as app.js, and a new module is covered
  // the day it is added. app.js is the one exception by design -- it is the boot script.
  const modules = fs.readdirSync(HERE).filter((n) => /.m?js$/.test(n) && !n.includes(".test.") && n !== "app.js");
  assert.ok(modules.length > 50, `the module scan found only ${modules.length}; this would pass vacuously`);
  for (const name of modules) {
    await assert.doesNotReject(import(pathToFileURL(path.join(HERE, name)).href), `${name} does not import in Node`);
  }
});

test("CONTROL: the import check fails on a module that reads the browser at load", async () => {
  await assert.rejects(import("data:text/javascript,export const t = document.title;"));
});

test("the purity check can actually SEE a module-scope browser global", () => {
  // Without this, the assertion above passes by matching nothing.
  //
  // The specimen used to be `const byId = (id) => document.getElementById(id);`, which was the wrong
  // one: that is a braceless ARROW BODY, deferred until the function is called, and the test directly
  // below already says such a body is fine. The two contradicted each other and the check sided with
  // this one -- so a module could be called unimportable for code that never runs on import, and the
  // only way to satisfy it was to reword a moved declaration and break the reconstruction proof's
  // byte-identity. Corrected when `byId` moved to ui.js in v0.5.4; this is now a real load-time read.
  const hits = moduleScopeBrowserRefs("const title = document.title;\n");
  assert.equal(hits.length, 1);
  assert.equal(hits[0].global, "document");
});

test("the purity check ignores browser globals INSIDE a function body", () => {
  // A function that touches the DOM when CALLED is fine; only module scope runs on import.
  assert.deepEqual(moduleScopeBrowserRefs("function f() {\n  return document.title;\n}\n"), []);
});
test("functionSpan finds a whole brace-matched body, not the first closing brace", () => {
  const src = "function outer(a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\nfunction after() {}\n";
  const span = functionSpan(src, "outer");
  assert.match(span.text, /return 2;/, "the span must run to the function's own closing brace");
  assert.doesNotMatch(span.text, /function after/);
});


// ── the declaration FORMS this parser can see ────────────────────────────────────────────────
//
// A form it does not match returns null, and null reads as "no such declaration" rather than "this
// parser cannot see that shape". `class` was such a form until 2026-08-16 — in the parser the repo
// mandates for every JS measurement — so the five session classes in the bridge measured as absent,
// and a size scan over those files reported almost nothing. These fixtures are what make the set of
// covered spellings a checked fact instead of whatever the regex happened to allow.

test("declarationSpan spans a CLASS, in every spelling", () => {
  const body = '{\n  method() {\n    return { nested: 1 };\n  }\n}\n';
  for (const head of ["class Foo ", "export class Foo ", "export default class Foo "]) {
    const span = declarationSpan(head + body, "Foo");
    assert.ok(span, `${head.trim()} was not found`);
    assert.equal(span.start, 0);
    assert.equal(span.end, 4, "the span ends on the class's closing brace, not the first inner one");
  }
});

test("declarationSpan spans generator and default-export functions", () => {
  for (const head of [
    "function* gen() ", "export function* gen() ", "export async function* gen() ",
    "async function *gen() ", "export default function gen() ",
  ]) {
    const span = declarationSpan(`${head}{\n  return 1;\n}\n`, "gen");
    assert.ok(span, `${head.trim()} was not found`);
    assert.equal(span.end, 2);
  }
});

test("widening the head patterns did not make them match a bare identifier", () => {
  // `function(?:\s+|\s*\*\s*)NAME` must not collapse to `functionNAME`, and `class` must not match a
  // word merely containing it. A false POSITIVE here is worse than the null it replaced: it would
  // hand a caller a span belonging to something else entirely.
  assert.equal(declarationSpan("export const x = functionok();\n", "ok"), null);
  assert.equal(declarationSpan("const classFoo = 1;\n", "Foo"), null);
  assert.equal(declarationSpan("// class Foo is coming next slice\n", "Foo"), null);
  assert.equal(declarationSpan("const gen = 1;\n", "gen").end, 0, "a real const still wins");
});

test("the four bridge classes are measurable, and the sizes are cross-checked", () => {
  // Not a synthetic case: these are the declarations the old parser was blind to. `PiSession` at 960
  // lines matches the figure recorded independently while measuring that file by other means, which
  // is what makes this a correctness check rather than merely a non-null one.
  const read = (rel) =>
    fs.readFileSync(path.join(HERE, "..", "..", rel), "utf8").replace(/\r\n/g, "\n");
  const cases = [
    ["mcp/stdio/pi-session.js", "PiSession", 960],
    ["mcp/stdio/codex-session.js", "CodexSession", 684],
    ["mcp/stdio/hermes-session.js", "HermesSession", 529],
    // A FIFTH CASE LIVED HERE, `TerminalProcessManager` in `mcp/stdio/terminal-runtime.js`, and it
    // was the most-revised row in this list -- 626 -> 910 across v0.6 Phase 8, every step
    // re-measured by brace-matching rather than copied out of a failure message. The whole file was
    // deleted on 2026-09-05 with the environment-bridge tier v0.6.2 retired: it ran PTYs for spawns
    // aify-comms no longer hosts, and no production path reached it.
    //
    // FOUR CASES STILL CROSS-CHECK THE PARSER, which is what this test is for. The point was never
    // the fifth class; it was that `declarationSpan` measures real declarations the old parser could
    // not see, and four independently-measured spans establish that as well as five did.
  ];
  for (const [rel, name, expected] of cases) {
    const span = declarationSpan(read(rel), name);
    assert.ok(span, `${name} in ${rel} is still invisible`);
    assert.equal(span.end - span.start + 1, expected, `${name} span moved; re-measure before editing`);
  }
});
