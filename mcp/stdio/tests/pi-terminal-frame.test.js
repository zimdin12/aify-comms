// The pi renderer — and an agreement test against the second copy of it that exists in this bridge.
//
// `hermes-acp-protocol.js` carries its OWN `colorize` and `briefJsonInline`, plus its own `ANSI`,
// `MAX_TOOL_INPUT_BRIEF_CHARS` and `MAX_TOOL_RESULT_BRIEF_CHARS`. Today `colorize` is byte-identical and
// `briefJsonInline` is the same logic written with different braces, and the three constants have equal
// values.
//
// THEY ARE DELIBERATELY NOT SHARED. Two runtimes' console renderers may legitimately diverge — pi and ACP
// are different protocols and one may need a format the other must not adopt — so unifying them is a
// decision for the reviewer rather than a cleanup I should perform while extracting. What is NOT a decision
// is silent drift: nobody reviewing a change to one would think to look at the other, and the symptom would
// be two runtimes' consoles quietly formatting the same thing differently. So this asserts they agree, and
// fails if one is edited without the other. That is the promotion rule for duplication — an agreement test,
// not a refactor.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ANSI, appendBounded, boundText, colorize } from "../pi-terminal-frame.mjs";
import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (f) => fs.readFileSync(path.join(STDIO, f), "utf-8");

// Pull one declaration's full text out of a module by brace matching from its `function`/`const` line.
function declText(file, kind, name) {
  const lines = read(file).split("\n");
  const i = lines.findIndex((l) => l.trimStart().startsWith(`${kind} ${name}`)
    || l.startsWith(`export ${kind} ${name}`));
  assert.notEqual(i, -1, `${name} not found in ${file}`);
  let depth = 0; let started = false;
  for (let j = i; j < lines.length; j += 1) {
    for (const ch of lines[j]) {
      if (ch === "{") { depth += 1; started = true; } else if (ch === "}") depth -= 1;
    }
    if (started && depth === 0) return lines.slice(i, j + 1).join("\n").replace(/^export /, "");
  }
  throw new Error(`unterminated ${name} in ${file}`);
}

test("BOUNDING IS THE POINT — every rendered string has a cap and says when it hit one", () => {
  // These strings reach a browser terminal and a model's context. An unbounded tool result is not a
  // rendering blemish, it is a frame that costs whoever reads it.
  assert.equal(boundText("abcdef", 4).length, 4, "the cap is a hard length, not a suggestion");
  assert.equal(boundText("abc", 10), "abc", "…and short text is untouched");
  assert.equal(boundText("", 4), "", "empty stays empty");
  // Degenerate caps must not throw or produce negative slices.
  for (const limit of [0, -1, undefined, null]) {
    const out = boundText("abcdef", limit);
    assert.equal(typeof out, "string", `a limit of ${JSON.stringify(limit)} must still yield a string`);
  }
});

test("appendBounded stops growing a buffer once it is full", () => {
  // The captured-error buffer uses this; without the bound a crash loop grows a string until the process
  // notices in the worst possible way.
  // The third argument is an OPTIONS OBJECT, not a number — passing `100` silently falls through to the
  // default cap, which is how my first version of this test "passed" a bound it never set.
  let buf = "";
  for (let i = 0; i < 500; i += 1) buf = appendBounded(buf, "0123456789", { limit: 100 });
  assert.equal(buf.length, 100, "the buffer must sit exactly at its cap, not grow");
  // And it keeps the TAIL: for a crash capture, the newest output is the part worth having.
  assert.ok(buf.endsWith("0123456789"), "the most recent chunk must survive");
  // With no options at all it still bounds, at the module's OWN declared default — read from the source
  // rather than guessed. My first attempt asserted `< 20000` against a cap that is exactly 20000, which is
  // the kind of threshold that passes or fails on which side of the boundary you happened to imagine.
  const declaredCap = Number(read("pi-terminal-frame.mjs")
    .match(/^const MAX_PI_ERROR_CAPTURE_CHARS = (\d+);/m)?.[1]);
  assert.ok(Number.isFinite(declaredCap) && declaredCap > 0, "the module must declare a capture cap");
  let unbounded = "";
  for (let i = 0; i < (declaredCap / 10) + 500; i += 1) unbounded = appendBounded(unbounded, "0123456789");
  assert.equal(unbounded.length, declaredCap,
    "with no options the default cap must apply exactly, not merely approximately");
});

test("colorize wraps in a reset so one coloured frame cannot bleed into the next", () => {
  // A missing reset is invisible in the test that produced it and visible in every line after it.
  const out = colorize(ANSI.red ?? "[31m", "x");
  assert.match(out, /\[0m$/, "must end with the ANSI reset");
  assert.ok(out.includes("x"), "…and still contain the text");
});

test("AGREEMENT: the duplicate `colorize` in hermes-acp-protocol.js is still identical", () => {
  assert.equal(
    declText("pi-terminal-frame.mjs", "function", "colorize"),
    declText("hermes-acp-protocol.js", "function", "colorize"),
    "the two colorize copies have diverged — reconcile them or make the difference deliberate",
  );
});

test("AGREEMENT: the duplicate `briefJsonInline` still BEHAVES identically", () => {
  // Not byte-identical — same logic, different brace style — so this compares behaviour rather than text.
  // Both are module-private, so the comparison runs each copy's source in isolation rather than importing.
  const run = (file) => {
    const src = declText(file, "function", "briefJsonInline");
    // eslint-disable-next-line no-new-func
    return new Function(`${src}; return briefJsonInline;`)();
  };
  const a = run("pi-terminal-frame.mjs");
  const b = run("hermes-acp-protocol.js");
  const cases = [
    [undefined, 10], [null, 10], ["", 10], ["short", 10],
    ["a much longer string than the limit allows", 10],
    ["   collapse   these    spaces   ", 40],
    [{ a: 1, b: [2, 3] }, 10], [{ a: 1 }, 100], [[1, 2, 3], 5],
    [123, 10], [true, 10], ["exactlyten", 10], ["exactlyten!", 10],
  ];
  for (const [value, limit] of cases) {
    assert.equal(a(value, limit), b(value, limit),
      `the two briefJsonInline copies disagree on ${JSON.stringify(value)} @ ${limit}`);
  }
});

test("AGREEMENT: the duplicated caps still hold the same values", () => {
  // Equal today. If one is tuned and the other is not, two runtimes truncate at different points and the
  // difference shows up as "the console looks different for pi" with no obvious cause.
  const pi = read("pi-terminal-frame.mjs");
  const acp = read("hermes-acp-protocol.js");
  for (const name of ["MAX_TOOL_INPUT_BRIEF_CHARS", "MAX_TOOL_RESULT_BRIEF_CHARS"]) {
    const grab = (src) => src.match(new RegExp(`^const ${name} = (\\d+);`, "m"))?.[1];
    const a = grab(pi); const b = grab(acp);
    assert.ok(a, `${name} not found in pi-terminal-frame.mjs`);
    assert.equal(a, b, `${name} differs: pi=${a} acp=${b}`);
  }
  // ANSI is a table; compare the parsed key/value pairs rather than the text, since either could be
  // reformatted without meaning anything.
  const table = (src) => {
    const body = src.match(/^const ANSI = \{([\s\S]*?)^\};/m)?.[1] ?? "";
    return new Map([...body.matchAll(/(\w+):\s*"([^"]*)"/g)].map((m) => [m[1], m[2]]));
  };
  // NOT equal, and that is fine: pi's table has twelve entries and ACP's nine — ACP simply needs fewer
  // colours. What must hold is that the names they SHARE mean the same escape code, since a colour that
  // rendered differently per runtime is the drift worth catching. Asserting equality here would have been
  // asserting a coincidence, and it failed the moment I wrote it.
  const shared = [...table(pi).keys()].filter((k) => table(acp).has(k));
  assert.ok(shared.length >= 8, `expected a substantial shared palette, got ${shared.length}`);
  for (const key of shared) {
    assert.equal(table(pi).get(key), table(acp).get(key),
      `ANSI.${key} differs between the two renderers`);
  }
});

test("exactly one module declares each moved name, and pi-session still renders through it", () => {
  for (const name of ["formatPiEventAsTerminalFrame", "boundText", "appendBounded", "formatTokenUsage"]) {
    assert.deepEqual(declaringModules(name), [{ file: "pi-terminal-frame.mjs", kind: "function" }],
      `${name} must be declared exactly once, by its owner`);
  }
  assert.ok(isUsedInBridge("formatPiEventAsTerminalFrame"), "the session must still render frames");
  const session = read("pi-session.js");
  assert.match(session, /from "\.\/pi-terminal-frame\.mjs"/, "pi-session.js imports the renderer back");
  assert.doesNotMatch(session, /^const ANSI = \{/m, "…and must not keep its own copy of the colour table");
});

test("the owner is pure — it renders, it does not run anything", () => {
  const src = read("pi-terminal-frame.mjs");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  assert.doesNotMatch(src, /spawn|child_process|setInterval|setTimeout|httpCall/,
    "rendering must not reach a process, a timer or the network");
  assert.deepEqual([...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]), ["./runtimes.js"]);
});
