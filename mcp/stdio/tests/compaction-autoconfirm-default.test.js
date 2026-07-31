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

test("compaction auto-confirm is opt-IN (=== '1'), never opt-out", () => {
  assert.match(
    source,
    /autoConfirmCompaction:\s*process\.env\.AIFY_AUTO_CONFIRM_COMPACTION\s*===\s*"1"/,
    "auto-confirm must require an explicit opt-in",
  );
});

test("the opt-OUT form is gone", () => {
  assert.doesNotMatch(
    source,
    /AIFY_AUTO_CONFIRM_COMPACTION\s*!==\s*"0"/,
    'the `!== "0"` form enables compaction auto-confirm everywhere by default — that is the ' +
      "defect this test exists to prevent from returning",
  );
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
