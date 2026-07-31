// The resume dialog as claude ACTUALLY renders it today (3 options, summary FIRST).
//
// Operator-reported data loss 2026-08-01: "auto compaction (select first option not keep as)".
// Root cause was dialog drift — our fixtures encoded a 2-option menu with "Resume full session
// as-is" FIRST, while the live dialog is (docs: code.claude.com/docs/en/sessions):
//
//   1. Resume from summary          <- runs /compact, loses detail
//   2. Resume full session as-is    <- operator policy: ALWAYS this one
//   3. Don't ask me again
//
// These tests pin the ONLY thing that matters: whatever the keystrokes are, they must land on
// "Resume full session as-is" — never on summary, never on "Don't ask me again".

import assert from "node:assert/strict";
import { matchConsolePrompt } from "../claude-console-prompts.js";

const DOWN = "\x1b[B";
const UP = "\x1b[A";
const ENTER = "\r";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// Simulate arrow navigation over the option rows and report where Enter lands.
function landsOn(frameLines, keys) {
  const opts = frameLines.filter((l) => /(Resume from summary|Resume full session|Don'?t ask me again)/i.test(l));
  let idx = opts.findIndex((l) => /[❯›▶]/.test(l));
  assert.ok(idx >= 0, "fixture must have a cursor on an option row");
  for (const k of keys) {
    if (k === DOWN) idx = Math.min(idx + 1, opts.length - 1);
    else if (k === UP) idx = Math.max(idx - 1, 0);
  }
  assert.equal(keys[keys.length - 1], ENTER, "the sequence must end with Enter");
  return opts[idx];
}

const CURRENT_NUMBERED = [
  "This session is large.",
  "",
  "❯ 1. Resume from summary",
  "  2. Resume full session as-is",
  "  3. Don't ask me again",
];

const CURRENT_UNNUMBERED = [
  "This session is large.",
  "",
  "❯ Resume from summary",
  "  Resume full session as-is",
  "  Don't ask me again",
];

test("REGRESSION: numbered 3-option menu selects 'Resume full session as-is', not summary", () => {
  const rule = matchConsolePrompt(CURRENT_NUMBERED.join("\n"));
  assert.ok(rule, "the dialog must be recognised at all");
  assert.match(landsOn(CURRENT_NUMBERED, rule.answer), /Resume full session as-is/i);
});

test("REGRESSION: UNNUMBERED 3-option menu also lands on 'Resume full session as-is'", () => {
  // This is the case the old NUMBERED_MENU_OPTION_RE could not count: moves collapsed to 0.
  const rule = matchConsolePrompt(CURRENT_UNNUMBERED.join("\n"));
  assert.ok(rule, "an unnumbered menu must still be answerable");
  assert.match(landsOn(CURRENT_UNNUMBERED, rule.answer), /Resume full session as-is/i);
});

test("never lands on 'Don't ask me again' (it disables the dialog fleet-wide)", () => {
  for (const frame of [CURRENT_NUMBERED, CURRENT_UNNUMBERED]) {
    const rule = matchConsolePrompt(frame.join("\n"));
    if (!rule) continue;
    assert.doesNotMatch(landsOn(frame, rule.answer), /Don'?t ask me again/i);
  }
});

test("cursor ALREADY on the keep option presses Enter without moving", () => {
  const frame = [
    "This session is large.",
    "",
    "  1. Resume from summary",
    "❯ 2. Resume full session as-is",
    "  3. Don't ask me again",
  ];
  const rule = matchConsolePrompt(frame.join("\n"));
  assert.ok(rule);
  assert.deepEqual(rule.answer, [ENTER], "no movement needed when already on the target");
  assert.match(landsOn(frame, rule.answer), /Resume full session as-is/i);
});

test("a PARTIAL frame (target not yet painted) refuses to press", () => {
  // The documented mid-render hazard: summary paints before full-session. Pressing here is the
  // data loss. No rule, or a rule that cannot compute an answer, must mean NO keystrokes.
  const partial = ["This session is large.", "", "❯ 1. Resume from summary"].join("\n");
  const rule = matchConsolePrompt(partial);
  assert.equal(rule, null, "must not answer a menu whose keep-option has not rendered yet");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (e) { failed += 1; console.log(`  FAIL ${name}`); console.log(`       ${e.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} resume-dialog layout tests passed`);
if (failed) process.exit(1);
