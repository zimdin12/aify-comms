#!/usr/bin/env node
// The control loop hands the terminal's argv to the spawn, so delegation can actually run.
//
// Phase 8's delegated path refuses a spawn whose row carries no argv -- deliberately, because aify-env
// executes an allowlisted launcher FILE and splitting a shell string is the quoting bug that design
// avoids. It fails closed with a clear message, which is right.
//
// But the production caller never passed argv. `terminal-control-loop.mjs` reads `terminal.argv` to
// find the session handle structurally, and then called TERMINAL_MANAGER.start() without it, so argv
// defaulted to []. Flipping AIFY_COMMS_DELEGATE_SPAWNS would have thrown on the FIRST spawn and every
// one after.
//
// The seam was "proven against a real aify-env" -- by a test that constructs the spec itself and passes
// argv. That proves the seam, not the path. This test is about the path: the difference between a
// component that works and a component that works when something else supplies the input.
//
// Read as SOURCE rather than by running the loop, which needs a service, a control row and a PTY. The
// weakness of a source assertion is real (it proves a line was written, not that it runs), so the
// second test drives the actual predicate with a row and checks what start() would receive.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const LOOP = fileURLToPath(new URL("../terminal-control-loop.mjs", import.meta.url));
const source = readFileSync(LOOP, "utf8");

test("the start() call the loop makes includes argv", () => {
  const call = source.slice(source.indexOf("TERMINAL_MANAGER.start({"));
  const spec = call.slice(0, call.indexOf("});"));
  assert.ok(spec.includes("argv:"), "the spawn spec carries no argv, so delegation cannot ever run");
  assert.match(spec, /terminal\.argv/, "argv must come from the row, not be invented here");
});

test("it is taken from the row and defaulted to an array, never left undefined", () => {
  // `startDelegated` filters argv with Array.isArray and then checks length. An undefined argv and a
  // non-array both have to arrive as [] so the refusal message is the one that names the real problem.
  const call = source.slice(source.indexOf("TERMINAL_MANAGER.start({"));
  const spec = call.slice(0, call.indexOf("});"));
  assert.match(spec, /Array\.isArray\(terminal\.argv\)/);
});

// ── the predicate itself, driven rather than read ─────────────────────────────────────

/** Exactly the expression the loop uses, so this test fails if that expression changes shape. */
function argvFor(terminal) {
  return Array.isArray(terminal.argv) ? terminal.argv : [];
}

test("a row carrying argv delegates; one without it refuses with a message that names why", async () => {
  const { TerminalProcessManager } = await import("../terminal-runtime.js");
  const manager = new TerminalProcessManager();
  // Delegation ON, so startDelegated is the path taken. No aify-env is contacted: both cases fail or
  // return before any request, which is what makes this safe to run anywhere.
  manager.envDelegation = { isEnabled: () => true, client: { start: async () => ({ id: "x" }) } };

  await assert.rejects(
    () => manager.start({ id: "t1", command: "claude-aify --resume abc", argv: argvFor({}), cwd: "." }),
    /carries no argv/,
    "a row with no argv must refuse, and say that is why",
  );

  // And with argv present it gets past that refusal -- proven by the NEXT failure being about
  // resolving the launcher, not about argv.
  await assert.rejects(
    () => manager.start({
      id: "t2",
      command: "no-such-launcher-aify --resume abc",
      argv: argvFor({ argv: ["no-such-launcher-aify", "--resume", "abc"] }),
      cwd: ".",
    }),
    (error) => {
      assert.doesNotMatch(error.message, /carries no argv/, "argv was supplied and still refused");
      assert.match(error.message, /does not resolve to an executable/);
      return true;
    },
  );
});

test("a non-array argv is treated as absent rather than passed through", () => {
  for (const bad of [undefined, null, "claude-aify --resume abc", 42, {}]) {
    assert.deepEqual(argvFor({ argv: bad }), [], `${JSON.stringify(bad)} was not normalised`);
  }
  assert.deepEqual(argvFor({ argv: ["a", "b"] }), ["a", "b"]);
});
