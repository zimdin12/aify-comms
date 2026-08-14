// Dashboard terminal input must reach the PTY RAW, even while Claude is showing a prompt.
//
// This is an ABSENCE assertion — that the input branch does not route through Claude's prompt
// preparation or sleep before writing — which is the one shape a behavioural test cannot express well
// and a source check can. So it stays a source check, with the two faults it had fixed.
//
// FAULT 1: it read `server.js`. The input branch moved to `terminal-control-loop.mjs` in v0.5.4 with the
// terminal-control pass, and the test went red on a pure relocation.
//
// FAULT 2, and the worse one: it sliced with `indexOf` and never checked the result. When the markers
// were absent both calls returned -1, `slice` produced an EMPTY STRING, and `assert.doesNotMatch("")`
// passes — so half of this test would have gone on passing against a file that no longer contained the
// code at all. The slice is now asserted non-empty before anything is read from it.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../terminal-control-loop.mjs", import.meta.url), "utf8");

test("dashboard terminal input is raw even while Claude shows a prompt", () => {
  const from = source.indexOf('} else if (control.action === "input")');
  const to = source.indexOf('} else if (control.action === "resize")');
  assert.notEqual(from, -1, "the input branch must be findable — otherwise this test asserts nothing");
  assert.notEqual(to, -1, "the resize branch bounds the slice");
  assert.ok(to > from, "the branches must appear in order");

  const inputBranch = source.slice(from, to);
  assert.ok(inputBranch.trim().length > 0, "an empty slice would make the absence check vacuous");

  assert.doesNotMatch(inputBranch, /prepareClaudeTerminalInput|sleep\(/);
  assert.match(inputBranch, /TERMINAL_MANAGER\.input\(terminalId, rawBody\)/);
});
