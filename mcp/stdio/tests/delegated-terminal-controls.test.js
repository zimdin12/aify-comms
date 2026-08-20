#!/usr/bin/env node
// Every control path a dashboard uses must work on a DELEGATED terminal, not just a local pty.
//
// The manager's branches ask `kind === "pty"` and fall through to `state.proc` otherwise -- which is
// right for the pipe fallback and wrong for a delegated terminal, whose process lives in aify-env and
// which has a `term` shim instead. Found in review (resize) and widened by reading every branch:
//
//   input()  fell through to terminal.proc.stdin -- undefined, so a keystroke VANISHED
//   stop()   called terminal.proc.stdin.end()    -- undefined, so Stop THREW
//   resize() did nothing at all
//   the auth-failure kill passed state.proc      -- so a failing delegated agent was never killed
//
// The distinction that fixes it is not "which kind" but WHICH ABILITY: anything expressible through
// the `term` abstraction works for both, and anything that touches a LOCAL OS process must stay
// pty-only, because a delegated pid belongs to another process's children and is not ours to signal.

import assert from "node:assert/strict";
import { test } from "node:test";

import { TerminalProcessManager } from "../terminal-runtime.js";

/** An EnvClient stand-in that records what the manager asks the environment to do. */
function fakeClient(calls = []) {
  return {
    calls,
    async start() { return { ok: true, handle: { id: "env-1", pid: 4242, terminal: true } }; },
    async subscribeOutput() { return () => {}; },
    async write(id, data) { calls.push(["write", data]); return { ok: true }; },
    async resize(id, cols, rows) { calls.push(["resize", cols, rows]); return { ok: true }; },
    async stop(id) { calls.push(["stop", id]); return { ok: true }; },
  };
}

async function delegated(calls) {
  const manager = new TerminalProcessManager({
    envDelegation: { isEnabled: () => true, client: fakeClient(calls) },
  });
  await manager.start({
    id: "t1", command: process.execPath, argv: [process.execPath, "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });
  return manager;
}

const settle = () => new Promise((r) => setImmediate(r));

test("input reaches a delegated agent instead of vanishing", async () => {
  const calls = [];
  const manager = await delegated(calls);
  manager.input("t1", "hello\n");
  await settle();
  assert.deepEqual(calls.filter((c) => c[0] === "write"), [["write", "hello\n"]]);
});

test("resize reaches a delegated agent, and updates the recorded dimensions", async () => {
  const calls = [];
  const manager = await delegated(calls);
  manager.resize("t1", 111, 22);
  await settle();
  assert.deepEqual(calls.filter((c) => c[0] === "resize"), [["resize", 111, 22]]);
  const state = manager.terminals.get("t1");
  assert.equal(state.cols, 111, "the keepalive restores state dims; stale ones snap the console back");
  assert.equal(state.rows, 22);
});

test("resize still CLAMPS for a delegated terminal", async () => {
  // WSL2 has reported columns=131072, which crashed a local pty's ioctl. A delegated resize crosses a
  // network first, but it ends at a pty just the same.
  const calls = [];
  const manager = await delegated(calls);
  // 0 rows means UNSPECIFIED, not zero: `rows || 28` defaults first and the clamp then leaves it. I
  // expected the floor of 6 here and was wrong about the code, not the other way round.
  manager.resize("t1", 999999, 0);
  await settle();
  assert.deepEqual(calls.filter((c) => c[0] === "resize"), [["resize", 2000, 28]]);

  manager.resize("t1", 1, 1);
  await settle();
  assert.deepEqual(calls.at(-1), ["resize", 20, 6], "the lower floor is what keeps a grid usable");
});

test("stop halts the delegated process instead of throwing", async () => {
  const calls = [];
  const manager = await delegated(calls);
  const result = await manager.stop("t1");
  await settle();
  assert.equal(result.stopped, true);
  assert.ok(calls.some((c) => c[0] === "stop"), "the environment was never told to stop the process");
  assert.equal(manager.terminals.has("t1"), false, "the terminal stayed in the map after stop");
});

test("the console keepalive does NOT arm for a delegated terminal", async () => {
  // Stated as a test because it was nearly "fixed" the wrong way. The keepalive forces a repaint with
  // two resizes every 4s; over HTTP that would be 30 requests a minute per terminal, to nudge a pty
  // that now lives in another process. It is off here, and if it is ever wanted it belongs in aify-env.
  const calls = [];
  const manager = await delegated(calls);
  const state = manager.terminals.get("t1");
  assert.equal(typeof state.stopConsoleKeepalive, "function");
  await new Promise((r) => setTimeout(r, 250));
  assert.equal(calls.filter((c) => c[0] === "resize").length, 0, "the keepalive is resizing remotely");
});

test("a start whose output stream fails is REFUSED, not reported as attached", async () => {
  // Found in review. subscribeOutput returns null on no endpoint, a fetch failure, a non-200, or a
  // body that cannot be read -- and the start path ignored the answer. The result was the worst shape
  // available: a terminal reported "attached", registered locally, with a process running in aify-env
  // that nothing was listening to. No output would ever arrive and no exit would ever be delivered, so
  // the row would sit there looking healthy forever.
  const stopped = [];
  const client = {
    async start() { return { ok: true, handle: { id: "env-9", pid: 77, terminal: true } }; },
    async subscribeOutput() { return null; },
    async stop(id) { stopped.push(id); return { ok: true }; },
  };
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true, client } });

  await assert.rejects(
    () => manager.start({
      id: "t9", command: process.execPath, argv: [process.execPath, "--version"],
      cwd: process.cwd(), runtime: "claude-code",
    }),
    /stream|output/i,
  );

  assert.equal(manager.terminals.has("t9"), false, "a refused start left local state behind");
  assert.deepEqual(stopped, ["env-9"], "the process was left running in aify-env after the refusal");
});
