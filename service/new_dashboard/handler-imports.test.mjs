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
  for (const m of src.matchAll(/\(([^)]*)\)\s*=>/g)) {
    for (const p of m[1].split(",")) {
      const n = p.trim().split(/[=:\s]/)[0].replace(/^\.\.\./, "");
      if (n) known.add(n);
    }
  }
  for (const m of src.matchAll(/catch\s*\(\s*([A-Za-z_$][\w$]*)/g)) known.add(m[1]);
  for (const m of src.matchAll(/\b([A-Za-z_$][\w$]*)\s*=>/g)) known.add(m[1]);
  return known;
}

/**
 * Bare `name(` calls the module cannot resolve.
 *
 * CALL POSITION ONLY, and never after a dot. `ctl.render()` belongs to its object and says nothing about
 * this module's imports; a bare `toast(` that nothing defines is exactly the defect.
 */
function unresolvedCalls(src) {
  const known = knownNames(src);
  const found = new Map();
  src.split(NL).forEach((line, i) => {
    if (/^\s*(?:\/\/|\*|\/\*)/.test(line)) return;
    for (const m of line.matchAll(/(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) {
      if (!known.has(m[2]) && !found.has(m[2])) found.set(m[2], i + 1);
    }
  });
  return found;
}

const handlerModules = () =>
  fs.readdirSync(HERE).filter((f) => /-click-handlers\.mjs$/.test(f) && !f.includes(".test."));

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

test("the scan actually reaches the modules, and they are the ones this series created", () => {
  // Anti-vacuity for the check above: a rename of the suffix, or a directory read that stopped matching,
  // would make "no offenders" true for the wrong reason.
  const files = handlerModules();
  assert.ok(files.length >= 5, `expected the handler modules, found ${files.length}: ${files.join(", ")}`);
  for (const f of files) {
    const src = fs.readFileSync(path.join(HERE, f), "utf-8");
    assert.match(src, /^export function /m, `${f} must export the handlers it holds`);
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
