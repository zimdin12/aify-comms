// The attach line says whether the operator got a terminal, because until now nothing did.
//
// THE JOIN, and it spans three components. aify-env decides whether it can open a PTY
// (`terminalSupport()` -> node-pty resolves or does not) and returns `terminal: true|false` on the
// spawn. aify-comms' `startDelegated` reads that and returns `pty`. The terminal control loop, the
// only caller of that return, read `started.pid` and wrote `[terminal attached pid=N]` -- and the
// flag stopped there, one line short of the message the operator reads.
//
// The failure it leaves behind is the one aify-env's runner warns about in its own words: a caller
// that silently receives pipes when it expected a terminal "gets output that looks slightly wrong and
// no warning". The operator's standing requirement is a real TUI in the web console, so the console
// going quietly non-TUI is exactly the case that must not be silent.
//
// WHY THIS IS A UNIT TEST AND NOT A LOOP TEST. `terminal-control-loop.test.js` says it: "NOTHING HERE
// STARTS A TERMINAL. Every case ends at or before the workspace check." The attach message is past
// that point, so reaching it behaviourally means spawning a PTY. The behaviour lives in a pure module
// for that reason, and the call site is pinned separately below.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { attachNotice, ptyState } from "../terminal-attach-notice.js";

const STDIO = fileURLToPath(new URL("..", import.meta.url));

test("a real terminal attaches with the line it always had", () => {
  assert.equal(attachNotice({ pid: 43210, status: "attached", pty: true }), "[terminal attached pid=43210]\n");
});

test("piped stdio says so, on the line the operator reads", () => {
  const notice = attachNotice({ pid: 49060, status: "attached", pty: false });
  assert.match(notice, /^\[terminal attached pid=49060\]\n/, "the existing first line must survive");
  assert.match(notice, /no pty on this host/i, "the degradation was not stated");
  assert.match(notice, /will not render a TUI/i, "the consequence was not stated");
  assert.match(notice, /node-pty/i, "the cause was not named, so nobody can act on it");
});

test("an UNKNOWN pty flag says nothing rather than guessing", () => {
  // The deliberate exception to fail-closed, and the reason is in the module: warning on silence puts
  // "this console will not render a TUI" in front of an operator whose console renders one. The cost
  // of the wrong answer runs the other way here, because the console itself shows the truth.
  for (const started of [{ pid: 1 }, { pid: 1, pty: undefined }, { pid: 1, pty: "false" }, null, undefined]) {
    assert.doesNotMatch(attachNotice(started), /no pty/i, `warned on an unknown flag: ${JSON.stringify(started)}`);
  }
});

test("ptyState separates the three answers", () => {
  // Three, not two. The whole defect this replaces came from a boolean that could not say "nobody
  // told me", so the module keeps that distinction explicit rather than folding it into false.
  assert.equal(ptyState({ pty: true }), true);
  assert.equal(ptyState({ pty: false }), false);
  assert.equal(ptyState({}), null);
  assert.equal(ptyState(null), null);
  assert.equal(ptyState("nope"), null);
});

test("a missing pid still produces a line rather than the word undefined", () => {
  assert.equal(attachNotice({ pty: true }), "[terminal attached pid=]\n");
  assert.doesNotMatch(attachNotice({ pty: true }), /undefined|null/);
});

test("THE CALL SITE uses it, which is the half a helper test cannot prove", () => {
  // A pure helper with green tests is exactly what a disconnected call site hides: this repo shipped
  // an interrupt feature whose six tests all passed against a builder nothing called. The loop cannot
  // be run here without spawning a PTY, so the call site is pinned by reading it.
  const loop = readFileSync(join(STDIO, "terminal-control-loop.mjs"), "utf8");
  assert.match(loop, /attachNotice\(started\)/, "the control loop no longer asks for the attach notice");
  assert.doesNotMatch(
    loop,
    /output:\s*`\[terminal attached pid=\$\{started\.pid\}\]/,
    "the control loop is building the attach line itself again, so the pty flag is being dropped once more",
  );
});
