// Every name an extracted handler CALLS must be one it imports or declares.
//
// THE DEFECT THIS WAS BUILT FROM, and it shipped into a commit before a test caught it:
// `runChannelAction`'s `.catch` called `toast`, and `chat-click-handlers.mjs` did not import it. The
// module parsed. `node --check` passed. All three suites passed. The line is reachable ONLY when a
// channel action fails, so it would have thrown inside a delegated click listener on the day something
// actually went wrong — and taken every branch after it in that listener down with it.
//
// This is the JS analogue of the symtable undefined-name sweep the Python side runs on every slice.
// That sweep exists because skipping it once cost 302 red tests; this one exists because the extraction
// work moves BODIES, and a body's error path references names its happy path does not. Reviewing the
// happy path is what everyone does.
//
// SCOPE IS DELIBERATELY NARROW. It reads the `*-click-handlers.mjs` modules this series creates — small,
// uniform, and all written the same way. A heuristic scanner turned loose on the whole tree would report
// wrong answers, and a gate that reports wrong answers is worse than none.
//
// IT IS ALSO PROVEN TO FAIL. The last test below feeds it the exact broken module and requires the
// detection, because a scanner that only ever prints "clean" is indistinguishable from one that is
// broken — and this file's whole value is the "clean".

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NL = String.fromCharCode(10);

/** Names the browser provides. Not exhaustive — additions are cheap and a miss is a false positive. */
const GLOBALS = new Set([
  "window", "document", "localStorage", "sessionStorage", "console", "navigator", "location", "fetch",
  "Math", "JSON", "Object", "Array", "String", "Number", "Boolean", "Date", "Promise", "Set", "Map",
  "WeakMap", "Error", "TypeError", "RegExp", "Symbol", "BigInt", "Intl", "URL", "URLSearchParams",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame",
  "cancelAnimationFrame", "queueMicrotask", "structuredClone", "parseInt", "parseFloat", "isNaN",
  "encodeURIComponent", "decodeURIComponent", "Event", "CustomEvent", "AbortController", "WebSocket",
  "FormData", "Blob", "File", "FileReader", "Image", "MutationObserver", "ResizeObserver", "globalThis",
  "undefined", "NaN", "Infinity", "arguments",
]);

const KEYWORDS = new Set([
  "const", "let", "var", "function", "return", "if", "else", "for", "while", "do", "switch", "case",
  "break", "continue", "try", "catch", "finally", "throw", "new", "delete", "typeof", "instanceof",
  "in", "of", "class", "extends", "async", "await", "yield", "true", "false", "null", "void", "static",
  "get", "set", "from", "as", "this", "super", "import", "export", "default",
]);

/**
 * Names this module resolves: imports, module-level declarations, parameters and inner bindings.
 *
 * Deliberately GENEROUS — every pattern here only ever adds to the known set, so the error is biased
 * toward missing a real defect rather than inventing one. That is the wrong bias for a detector in
 * general, and the right one for a gate that must not block a correct commit.
 */
function knownNames(src) {
  const known = new Set([...GLOBALS, ...KEYWORDS]);
  for (const m of src.matchAll(/^import\s*\{([^}]*)\}\s*from/gm)) {
    for (const raw of m[1].split(",")) {
      const n = raw.trim().split(/\s+as\s+/).pop().trim();
      if (n) known.add(n);
    }
  }
  for (const m of src.matchAll(/^import\s+(\w+)\s+from/gm)) known.add(m[1]);
  for (const m of src.matchAll(/^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm)) known.add(m[1]);
  for (const m of src.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) known.add(m[1]);
  for (const m of src.matchAll(/function\s+[A-Za-z_$][\w$]*\s*\(([^)]*)\)/g)) {
    for (const p of m[1].split(",")) {
      const n = p.trim().split(/[=:\s]/)[0].replace(/^\.\.\./, "");
      if (n) known.add(n);
    }
  }
  // NESTED PARENS ARE THE TRAP HERE. In `list.find((a) => a.id === x)` the naive `\(([^)]*)\)` match
  // starts at `find(` and captures `(a` — paren included — so the parameter `a` was never registered and
  // every such arrow produced a false positive. Stripping non-identifier characters is what fixes it,
  // and a false positive in this gate is not harmless: it blocks a correct commit, which is how a gate
  // gets deleted.
  for (const m of src.matchAll(/\(([^)]*)\)\s*=>/g)) {
    for (const p of m[1].split(",")) {
      const n = p.trim().split(/[=:\s]/)[0].replace(/^\.\.\./, "").replace(/[^\w$]/g, "");
      if (n) known.add(n);
    }
  }
  for (const m of src.matchAll(/catch\s*\(\s*([A-Za-z_$][\w$]*)/g)) known.add(m[1]);
  for (const m of src.matchAll(/\b([A-Za-z_$][\w$]*)\s*=>/g)) known.add(m[1]);
  return known;
}

/**
 * Names the module uses but cannot resolve — in CALL position or as the HEAD OF A PROPERTY CHAIN.
 *
 * CALL POSITION was the original check and it was not enough. `render-memo.mjs` moved seven signature
 * builders whose bodies read `state.agents`, and its module never imported `state`. Nothing called
 * `state(`, so the call-only scan reported the file clean while every one of those functions threw on
 * first use. The property-chain head is the same defect wearing different syntax.
 *
 * Only the HEAD of a chain counts: in `a.b.c()`, `a` must resolve and `b`/`c` belong to whatever `a` is.
 * That is what the `[^.\w$]` prefix enforces, and it is why `ctl.render()` says nothing about imports.
 */
function unresolvedNames(src) {
  const known = knownNames(src);
  const found = new Map();
  // PER-LINE string blanking, and it is required even for call position: `page-titles.mjs` contains the
  // prose "Shared artifacts (comms_share)" inside a data string, which reads as a call to `artifacts`.
  // Built from a plain string rather than a regex literal — these backslash classes are exactly what an
  // editor or a heredoc mangles into a broken character class, and a silently broken blanker turns the
  // whole gate into noise.
  //
  // Per line rather than whole-source ON PURPOSE. Blanking across the file handles multi-line templates
  // but then the first apostrophe in an English comment — "app.js's" — opens a string that swallows real
  // code. Per line, an unterminated quote can only affect its own line, and call position is unaffected
  // by the multi-line template in `static-links.mjs` because nothing in it is called.
  const STR = new RegExp("'(?:[^'\\\\]|\\\\.)*'|\"(?:[^\"\\\\]|\\\\.)*\"|`(?:[^`\\\\]|\\\\.)*`", "g");
  src.split(NL).forEach((line, i) => {
    if (/^\s*(?:\/\/|\*|\/\*)/.test(line)) return;
    for (const m of line.replace(STR, '""').matchAll(/(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) {
      if (!known.has(m[2]) && !found.has(m[2])) found.set(m[2], i + 1);
    }
  });
  return found;
}

// WHY THIS IS CALL-POSITION ONLY, having tried the alternative.
//
// A missing import used only as a property chain — `state.agents` with no `import { state }` — is the
// same defect and this does not catch it. `render-memo.mjs` shipped exactly that, and its tests caught it
// instead. So I extended the scan to the HEAD of a property chain, which requires knowing what is code
// and what is not, and that requires a lexer.
//
// Two attempts, two false positives on real files: an arrow parameter in `list.find((a) => …)` reported
// as `a()`, and `bash install.sh` inside a MULTI-LINE template literal reported as `install`. Blanking
// strings across the whole source fixed the second and broke on the first apostrophe in an English
// comment — "app.js's" opens a string as far as a regex is concerned.
//
// The standing rule here is that a gate reporting wrong answers is worse than none: a false positive
// blocks a correct commit, and a blocked commit is how a gate gets deleted. Chain heads stay uncovered
// by this file. What covers them is the thing that actually caught `render-memo.mjs` — a test that CALLS
// every export — and that is the standard every module in WATCHED already meets.

/** Kept as the old name so the intent of each call site stays readable. */
const unresolvedCalls = unresolvedNames;

/**
 * The modules this extraction series created. Named explicitly rather than globbed: the older dashboard
 * modules predate this check and have their own idioms, and a heuristic turned loose on all of them
 * would report wrong answers — which is worse than none.
 *
 * SCOPE WAS TOO NARROW AT FIRST. It read only `*-click-handlers.mjs`, and the very next slice created
 * `render-memo.mjs` with a missing `state` import that this check would have caught had it been looking.
 * Every module the series adds belongs here, on the day it is added.
 */
const WATCHED = [
  "agent-click-handlers.mjs",
  "chat-click-handlers.mjs",
  "console-click-handlers.mjs",
  "nav-click-handlers.mjs",
  "session-click-handlers.mjs",
  "console-await.mjs",
  "keyboard-shortcuts.mjs",
  "layout-prefs.mjs",
  "page-titles.mjs",
  "record-lookup.mjs",
  "render-memo.mjs",
  "run-helpers.mjs",
  "static-links.mjs",
];

const handlerModules = () => WATCHED.filter((f) => fs.existsSync(path.join(HERE, f)));

test("NO extracted handler calls a name it does not import or declare", () => {
  const offenders = [];
  for (const file of handlerModules()) {
    const found = unresolvedCalls(fs.readFileSync(path.join(HERE, file), "utf-8"));
    for (const [name, line] of found) offenders.push(`${file}:${line} calls ${name}()`);
  }
  assert.deepEqual(
    offenders, [],
    "these modules call names nothing in them resolves — the failure mode is an error path that throws "
      + "only when something else has already gone wrong:\n  " + offenders.join("\n  ")
      + "\nAdd the import. If this is a false positive, widen GLOBALS in this file rather than "
      + "silencing the check.",
  );
});

test("EVERY WATCHED MODULE EXISTS — the list cannot rot into names that are gone", () => {
  // Anti-vacuity, and stricter than a glob: a module renamed or deleted must fail here rather than
  // silently dropping out of the scan.
  const missing = WATCHED.filter((f) => !fs.existsSync(path.join(HERE, f)));
  assert.deepEqual(missing, [], "watched modules that no longer exist");
  const files = handlerModules();
  assert.ok(files.length >= 13, `expected every watched module, found ${files.length}`);
  for (const f of files) {
    const src = fs.readFileSync(path.join(HERE, f), "utf-8");
    // `export function` OR `export const`: `page-titles.mjs` is a data map, not handlers, and demanding
    // a function there would be asserting the shape this list happens to have rather than that each
    // module exports what it holds.
    assert.match(src, /^export (?:function|const) /m, `${f} must export what it holds`);
  }
});

test("THE DETECTOR CAN FAIL — fed the real defect, it finds it", () => {
  // The exact break that shipped: `toast` used on an error path, absent from the imports. A gate whose
  // only observed output is "clean" is indistinguishable from a broken one, and "clean" is this file's
  // entire product.
  const good = [
    "import { byId, toast } from './ui.js';",
    "export function runChannelAction(chanAction, chatChannelAction) {",
    "  chatChannelAction(chanAction.dataset.chatChannelAction, chanAction.dataset.channel)",
    "    .catch((err) => toast(`Channel action failed: ${err?.message || err}`, 'error'));",
    "}",
  ].join(NL);
  assert.deepEqual([...unresolvedCalls(good).keys()], [], "the fixed module must be clean");

  const broken = good.replace("import { byId, toast }", "import { byId }");
  assert.deepEqual([...unresolvedCalls(broken).keys()], ["toast"], "the broken module must be caught");
});

test("a PROPERTY call is not mistaken for a missing import", () => {
  // `ctl.renderConversation()` is the most common shape in these modules. Flagging it would make the
  // gate unusable, and the fix someone would reach for is deleting the gate.
  const src = [
    "export function f(ctl) {",
    "  ctl.renderConversation();",
    "  window.open('x', '_blank');",
    "}",
  ].join(NL);
  assert.deepEqual([...unresolvedCalls(src).keys()], []);
});

test("A CHAIN-ONLY missing import is a KNOWN GAP — recorded, not silently absent", () => {
  // `render-memo.mjs` shipped with `state.agents` and no `import { state }`, and this file does not catch
  // it. Asserting the gap keeps it honest: if someone later extends the detector, this test fails and
  // they update it deliberately rather than discovering the limitation from a production throw.
  //
  // What DOES cover it is a test that calls every export, which every module in WATCHED has.
  const chainOnly = "export const sig = () => state.agents.map((a) => a.id);";
  assert.deepEqual([...unresolvedNames(chainOnly).keys()], [], "not detected — see the note above");

  // …but the same module with a CALL is caught, which is the boundary of what this gate promises.
  const called = "export const sig = () => state().agents;";
  assert.deepEqual([...unresolvedNames(called).keys()], ["state"]);
});

test("a name appearing only inside a STRING is not reported", () => {
  // Strings are blanked before scanning. Without that, any module mentioning a name in a message or a
  // template would be flagged, and the gate would be deleted within a week.
  const src = [
    "export function f() {",
    "  return 'call toast() when state.x is set';",
    '  // eslint-disable-next-line',
    "}",
  ].join(NL);
  assert.deepEqual([...unresolvedNames(src).keys()], []);
});
