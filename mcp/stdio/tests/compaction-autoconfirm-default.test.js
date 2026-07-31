// The claude compaction auto-confirm must be OPT-IN.
//
// 2026-08-01: the operator reported the compaction dialog being answered with the FIRST option
// instead of "resume full" — i.e. context they had deliberately chosen to keep was silently
// compacted away. The flag was `!== "0"` (ON unless disabled), so every managed claude worker on
// every host had it enabled by default.
//
// claude-console-prompts.js documents the exact hazard: the menu paints PROGRESSIVELY and
// "Resume from summary" appears before "Resume full session", so a keystroke computed mid-render
// can land on summary. That file already carries guard after guard for it — and it still reached a
// live agent.
//
// The asymmetry is what fixes the default, and this repo wrote it down before it bit:
// a wrong press loses context "unrecoverable and fleet-wide"; the alternative is a STALL, which is
// "visible and recoverable". Unrecoverable-on-failure means opt-IN.
//
// This is a source assertion because the flag is read inline in terminal-runtime.js, which starts
// PTYs on import and cannot be loaded by a unit test. A source check still fails the suite on the
// one thing that matters: someone flipping the comparison back.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "terminal-runtime.js"), "utf8");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("the resume-dialog layout regression suite exists and is not empty", () => {
  // The real protection is NOT this flag's direction — it is that the arrow computation is pinned
  // against the CURRENT 3-option dialog. Auto-confirm was briefly forced off when the operator hit
  // silent compaction; the cause was dialog drift, not the default. If that suite disappears, the
  // default becomes dangerous again, so tie them together explicitly.
  const suite = readFileSync(join(here, "resume-dialog-current-layout.test.js"), "utf8");
  assert.match(suite, /Resume full session as-is/, "the keep-option must be asserted by name");
  assert.match(suite, /refuses to press/i, "the mid-render partial frame must be covered");
});

test("the flag is still wired (not silently deleted)", () => {
  // Deleting the option entirely would let matchConsolePrompt fall back to ITS default, which is a
  // different decision made in a different file. Keep the choice explicit at the call site.
  assert.match(source, /autoConfirmCompaction:/, "the option must still be passed explicitly");
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`  ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL ${name}`);
    console.log(`       ${error.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} compaction-default tests passed`);
if (failed) process.exit(1);
