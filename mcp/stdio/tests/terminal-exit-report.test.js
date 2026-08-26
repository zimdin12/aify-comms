// A terminal reports HOW it ended, because until 2026-08-26 nothing did.
//
// THE JOIN, and it spans three files. node-pty hands `{exitCode, signal}` to the exit wiring in
// `terminal-runtime.js`, which spreads both into the exit detail as `code` and `signal`. Every real
// exit path does it -- the PTY at line 257, the DELEGATED aify-env process at 374, the piped child at
// 446, a forced stop at 836. `terminal-manager.mjs` then read only `detail.error.message` and posted
// an output marker plus a status, so both numbers died one hop short of `terminal_sessions`.
//
// The cost was paid in the open: sc-claude and sc-architect died mid-turn, the operator asked why,
// and every record said `status='stopped'` with an empty `error` and nothing else. The console tail
// could show what the agent was DOING when it stopped and nothing at all about the stopping.
//
// WHAT THIS FILE PINS is the body, not the transport. The hook calls a module-scoped `httpCall`, so
// asserting the POST would mean killing a real terminal; the decision lives in a pure module for
// exactly that reason, and the call site is pinned by reading it in the last test.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { exitReport } from "../terminal-exit-report.js";

const STDIO = fileURLToPath(new URL("..", import.meta.url));

test("a clean exit reports code 0 rather than reporting nothing", () => {
  // THE CASE A TRUTHINESS TEST DESTROYS, and the reason this file exists in this shape. Zero is a
  // clean exit and the most common value there is; `if (code)` would drop it and leave the record
  // saying only that the terminal stopped -- which is what every record said before this change.
  const body = exitReport({ code: 0, signal: null });
  assert.equal(body.exitCode, 0);
  assert.equal(body.status, "stopped");
  assert.ok(!("exitSignal" in body), "a null signal was sent as a value");
});

test("a non-zero exit carries its code", () => {
  assert.equal(exitReport({ code: 137, signal: null }).exitCode, 137);
  assert.equal(exitReport({ code: 1 }).exitCode, 1);
});

test("a signal kill reports the signal, and the two are separate answers", () => {
  // A signalled process reports a NULL code and a signal. "killed by SIGKILL" and "exited 0" are
  // different facts about a death, so they travel as different fields rather than one string.
  const body = exitReport({ code: null, signal: "SIGKILL" });
  assert.equal(body.exitSignal, "SIGKILL");
  assert.ok(!("exitCode" in body), "a null code was sent as a value");
});

test("a forced stop reports SIGTERM", () => {
  // terminal-runtime.js:836 stops a terminal with `{signal: "SIGTERM"}` and no code.
  const body = exitReport({ signal: "SIGTERM" });
  assert.equal(body.exitSignal, "SIGTERM");
  assert.equal(body.status, "stopped");
});

test("a spawn failure still reports the error, and claims no code", () => {
  // The one path that legitimately has neither: the process never ran. Its `error` is the account of
  // the death and must survive, and inventing a code here would be a number nobody reported.
  const body = exitReport({ error: new Error("ENOENT: spawn claude-aify") });
  assert.equal(body.status, "failed");
  assert.match(body.output, /\[terminal failed\] ENOENT: spawn claude-aify/);
  assert.ok(!("exitCode" in body) && !("exitSignal" in body));
});

test("nothing reported means the fields are ABSENT, not null", () => {
  // Absent is how a reader tells silence from a value, and how an older service ignores a field it
  // does not know. Sending `null` would make "nobody said" indistinguishable from a reported null.
  const body = exitReport({});
  assert.deepEqual(Object.keys(body).sort(), ["output", "status"]);
  assert.deepEqual(Object.keys(exitReport()).sort(), ["output", "status"]);
});

test("a non-numeric code is not forwarded as one", () => {
  // Defensive on the boundary the runtime owns: a string or NaN code would become a bogus integer
  // server-side, and a wrong exit code is worse than none because it reads as evidence.
  for (const code of ["0", NaN, Infinity, {}, undefined]) {
    assert.ok(!("exitCode" in exitReport({ code })), `forwarded a non-numeric code: ${String(code)}`);
  }
});

test("the exit marker text is unchanged, so the existing tail keeps working", () => {
  // The output column and every reader of it -- including the failure-line extractor -- key on these
  // exact strings. Adding fields beside them must not rewrite them.
  assert.equal(exitReport({ code: 0 }).output, "\n[terminal exited]\n");
  assert.equal(exitReport({ error: new Error("boom") }).output, "\n[terminal failed] boom\n");
});

test("THE CALL SITE uses it, which is the half a pure test cannot prove", () => {
  // This repo shipped an interrupt feature whose six tests all passed against a builder nothing
  // called. The exit hook cannot be run here without a terminal, so the call site is read.
  const manager = readFileSync(join(STDIO, "terminal-manager.mjs"), "utf8");
  assert.match(manager, /\.\.\.exitReport\(detail\)/, "the exit hook no longer builds its body here");
  assert.doesNotMatch(
    manager,
    /output:\s*error\s*\?\s*`\\n\[terminal failed\]/,
    "the exit hook is building the body inline again, so the exit code is being dropped once more",
  );
});

test("every exit path in the runtime supplies something to report", () => {
  // The producer side, read rather than assumed. If a new exit path is added that passes neither a
  // code nor a signal nor an error, this file's guarantee quietly stops holding for it.
  const runtime = readFileSync(join(STDIO, "terminal-runtime.js"), "utf8");
  const calls = [...runtime.matchAll(/_handleExit\([^,]+,\s*[^,]+,\s*(\{[^}]*\})/g)].map((m) => m[1]);
  // TWO INSTRUMENTS, REQUIRED TO AGREE, rather than one with slack under it. The regex above reads a
  // BRACE-LITERAL detail; a call site passing a variable, or a literal containing a nested object,
  // matches nothing and is skipped in silence. This counts the call sites a second way -- by the call
  // itself, whatever its argument shape -- and demands the same number, so a path this file cannot
  // read fails here instead of being quietly excluded from the guarantee above it.
  //
  // It replaces `calls.length >= 4` against a real 5. That ceiling had room for one exit path to
  // disappear without anything going red, which is the shape this repo's own size gates refuse: the
  // MEASURED value, not a comfortable margin above it.
  // `this._handleExit(` -- the CALL form. A bare `_handleExit(` also matches the method DEFINITION,
  // which made the first version of this cross-check report 6 against 5 and fail on a disagreement
  // that was its own. The cross-check caught my error rather than the code's, which is what a second
  // instrument is for.
  const everyCall = [...runtime.matchAll(/this\._handleExit\(/g)].length;
  assert.ok(everyCall >= 4, `only ${everyCall} _handleExit call sites found; the scan is broken`);
  assert.equal(
    calls.length,
    everyCall,
    `${everyCall} exit paths exist but only ${calls.length} have a detail this test can read. The `
      + "unread one is outside the guarantee this test claims to make -- widen the pattern rather "
      + "than lowering the count.",
  );
  for (const detail of calls) {
    assert.match(
      detail,
      /\bcode\b|\bsignal\b|\berror\b/,
      `an exit path reports nothing this module can forward: ${detail}`,
    );
  }
});
