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
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * A real launcher on disk: shebang plus the marker aify-env requires.
 *
 * These tests used `process.execPath` -- node itself -- which resolves fine and is NOT a launcher.
 * aify-env would have refused it, so the tests were passing on a spawn the environment could never
 * accept, and that gap is exactly where the Windows .cmd-shim defect hid. A fixture that satisfies the
 * real contract is what makes these tests mean something.
 */
function writeLauncherFixture(name) {
  const dir = mkdtempSync(join(tmpdir(), "aify-launcher-"));
  const file = join(dir, name);
  const eol = String.fromCharCode(10);
  writeFileSync(file, ["#!/usr/bin/env bash", 'HARNESS_WRAPPER_VERSION="0.6.0"', "exit 0", ""].join(eol));
  return file;
}

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
    id: "t1", command: writeLauncherFixture("probe-aify"), argv: [writeLauncherFixture("probe-aify"), "--version"],
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
      id: "t9", command: writeLauncherFixture("probe-aify"), argv: [writeLauncherFixture("probe-aify"), "--version"],
      cwd: process.cwd(), runtime: "claude-code",
    }),
    /stream|output/i,
  );

  assert.equal(manager.terminals.has("t9"), false, "a refused start left local state behind");
  assert.deepEqual(stopped, ["env-9"], "the process was left running in aify-env after the refusal");
});

test("a delegated terminal is not listed as a locally owned OS process", async () => {
  // Raised in review as a watch item, and it is sharper than a naming question. listOwnedSessions
  // feeds dead-PTY REPORTING, which asks whether a pid is alive ON THIS HOST. A delegated process
  // lives in aify-env, which may be another machine entirely -- so that pid either does not exist here
  // or, worse, belongs to something unrelated that happens to share the number. Either way the answer
  // would be about the wrong process.
  //
  // Liveness for a delegated terminal is aify-env's to report, and it does, on /health.
  const calls = [];
  const manager = await delegated(calls);
  assert.equal(manager.terminals.has("t1"), true, "the terminal is not registered; this proves nothing");
  assert.deepEqual(
    manager.listOwnedSessions().map((s) => s.terminalId),
    [],
    "a delegated pid was reported as an OS process this bridge owns",
  );
});

test("a delegated death carries its SIGNAL to the exit hook, not a hardcoded null", async () => {
  // THE CALL SITE, driven rather than read. This callback was
  // `(code) => this._handleExit(id, state, { code, signal: null })` -- honest while aify-env had no
  // signal to give, and a lie from the moment it did. Every managed agent's terminal is delegated, so
  // this one line decided the answer to "why did my agent die" for the entire fleet.
  //
  // A pure test of `exitReport` cannot see this: the builder would happily format a signal it is never
  // handed. This repo has already shipped a feature whose six tests all passed against a builder
  // nothing called, so the callback is invoked here for real.
  const exits = [];
  let deliverExit;
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput(id, onOutput, onExit) { deliverExit = onExit; return () => {}; },
    async stop() { return { ok: true }; },
  };
  const manager = new TerminalProcessManager({
    envDelegation: { isEnabled: () => true, client },
    onExit: async (id, detail) => { exits.push([id, detail]); },
  });
  await manager.start({
    id: "t-sig", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });

  assert.equal(typeof deliverExit, "function", "the manager never subscribed for an exit");
  // THE THIRD ARGUMENT IS THE CONTRACT NOW. `env-client.mjs` passes `{ observedExitFrame: true }`
  // only when aify-env actually sent an `event: exit` frame; without it the manager treats the end
  // of a stream as "we lost sight of it" and refuses to finalise. Omitting it here made this test
  // simulate a case that is no longer an exit -- which is the fix working, not a regression.
  deliverExit(null, "SIGKILL", { observedExitFrame: true });
  await new Promise((r) => setTimeout(r, 50));

  assert.equal(exits.length, 1, "the delegated exit never reached the exit hook");
  const [, detail] = exits[0];
  assert.equal(detail.signal, "SIGKILL", "the signal was dropped at the delegated call site");
  assert.equal(detail.code, null, "a signalled death was given an exit code it never had");
});

test("a delegated CLEAN exit still reports its zero and no signal", async () => {
  // The control beside it. Zero is the most common exit in the fleet and a fix that lost it would be
  // the same defect from the other side.
  const exits = [];
  let deliverExit;
  const client = {
    async start() { return { ok: true, handle: { id: "env-y", pid: 98, terminal: true } }; },
    async subscribeOutput(id, onOutput, onExit) { deliverExit = onExit; return () => {}; },
    async stop() { return { ok: true }; },
  };
  const manager = new TerminalProcessManager({
    envDelegation: { isEnabled: () => true, client },
    onExit: async (id, detail) => { exits.push(detail); },
  });
  await manager.start({
    id: "t-clean", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });
  deliverExit(0, "", { observedExitFrame: true });
  await new Promise((r) => setTimeout(r, 50));

  assert.equal(exits.length, 1);
  assert.equal(exits[0].code, 0);
  assert.ok(!exits[0].signal, "a process nothing killed was given a signal");
});

test("the delegated spawn labels the row with the AGENT id, or nothing", () => {
  // WHAT THE COLUMN IS FOR. aify-env's PROCESSES view has an AGENT column and fills it from the
  // `label` the caller sends. The operator's requirement, stated 2026-08-26: the agent name, and only
  // for managed work.
  //
  // The fallback was `agentId || id`, so a spawn with no agent id put the TERMINAL id --
  // `term_1787745672834_79e59600` -- under a heading that says AGENT. A string that is not an agent
  // name, presented as one. An empty label is the honest answer, and aify-env renders it as a dash.
  const started = [];
  const client = {
    async start(spec) { started.push(spec); return { ok: true, handle: { id: "env-1", pid: 1, terminal: true } }; },
    async subscribeOutput() { return () => {}; },
    async stop() { return { ok: true }; },
  };
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true, client } });
  const launcher = writeLauncherFixture("probe-aify");

  return manager.start({
    id: "t-labelled", command: launcher, argv: [launcher, "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed", agentId: "sc-coder",
  }).then(async () => {
    assert.equal(started.at(-1).label, "sc-coder", "the AGENT column would not name the agent");

    await manager.start({
      id: "t-anonymous", command: launcher, argv: [launcher, "--version"],
      cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
    });
    assert.equal(
      started.at(-1).label, "",
      `a spawn with no agent id sent ${JSON.stringify(started.at(-1).label)} as the AGENT name`,
    );
    assert.doesNotMatch(started.at(-1).label, /^term_/, "a terminal id was sent as an agent name");
  });
});


test("a stream that ends with the process STILL LISTED does not reach the exit hook", async () => {
  // THE ORPHAN FACTORY, driven through the real call site. The operator killed aify-env; every
  // delegated stream ended at once; the bridge finalised each terminal; the processes survived
  // because aify-env's shutdown deliberately leaves what it cannot confirm. The control plane has
  // said `stopped` about a live, owned process ever since.
  //
  // `delegated-exit.mjs` is tested on its own, and that is exactly the shape that has fooled this
  // repo before -- a proven builder nothing called. This drives the manager.
  const exits = [];
  let deliverExit;
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput(id, onOutput, onExit) { deliverExit = onExit; return () => {}; },
    async stop() { return { ok: true }; },
    // aify-env still owns it: the stream broke, the process did not end.
    async list() { return { ok: true, handle: { processes: [{ id: "env-x", pid: 99 }] } }; },
  };
  const manager = new TerminalProcessManager({
    envDelegation: { isEnabled: () => true, client },
    onExit: async (id, detail) => { exits.push([id, detail]); },
  });
  await manager.start({
    id: "t-lost", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });

  // No third argument: the stream ended without an exit frame.
  deliverExit(null, "");
  await new Promise((r) => setTimeout(r, 50));

  assert.equal(
    exits.length, 0,
    "a live, owned process was reported as an exit -- the control plane would mark it stopped and "
      + "nothing would ever collect it",
  );
  assert.ok(manager.terminals.has("t-lost"), "the terminal was dropped despite its process surviving");
});

test("a stream that ends with the process GONE does reach the exit hook", async () => {
  // The control. Holding every terminal open would trade an orphaned process for a row that never
  // closes, and the reconcilers would heal forever.
  const exits = [];
  let deliverExit;
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput(id, onOutput, onExit) { deliverExit = onExit; return () => {}; },
    async stop() { return { ok: true }; },
    async list() { return { ok: true, handle: { processes: [] } }; },
  };
  const manager = new TerminalProcessManager({
    envDelegation: { isEnabled: () => true, client },
    onExit: async (id, detail) => { exits.push([id, detail]); },
  });
  await manager.start({
    id: "t-gone", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });

  deliverExit(null, "");
  await new Promise((r) => setTimeout(r, 50));

  assert.equal(exits.length, 1, "a process aify-env no longer owns was left attached for ever");
});


test("a held terminal gets its stream back when the environment returns", async () => {
  // THE OTHER HALF OF HOLDING. Refusing to call a live process dead leaves the terminal DEAF --
  // attached, registered, and receiving nothing, which terminal-runtime.js calls the worst shape
  // available. Without this the previous fix trades one defect for another.
  //
  // Driven through the manager on a TICK, which is how the control loop calls it.
  const outputs = [];
  let deliverExit;
  let subscribeCalls = 0;
  let environmentUp = true;
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput(id, onOutput, onExit) {
      subscribeCalls += 1;
      deliverExit = onExit;
      return environmentUp ? () => {} : null;
    },
    async stop() { return { ok: true }; },
    async list() { return { ok: true, handle: { processes: [{ id: "env-x", pid: 99 }] } }; },
  };
  const manager = new TerminalProcessManager({
    envDelegation: { isEnabled: () => true, client },
    onOutput: async (id, text) => { outputs.push(text); },
  });
  await manager.start({
    id: "t-back", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });
  assert.equal(subscribeCalls, 1);

  // The environment goes away: the stream ends with no exit frame, and re-subscribing fails.
  environmentUp = false;
  deliverExit(null, "");
  await new Promise((r) => setTimeout(r, 20));
  const whileDown = await manager.reattachLostStreams();
  assert.deepEqual(whileDown.stillLost, ["t-back"], "a terminal we cannot re-attach must stay held");
  assert.deepEqual(whileDown.reattached, []);

  // It comes back, re-owning the same process, and the next tick recovers the stream.
  environmentUp = true;
  const whenUp = await manager.reattachLostStreams();
  assert.deepEqual(whenUp.reattached, ["t-back"], "the stream was never re-opened: the terminal is deaf");
  assert.ok(
    outputs.some((text) => text.includes("re-attached")),
    "the console was not told its terminal came back",
  );

  // AND IT DOES NOT KEEP TRYING. A terminal that is live again must leave the lost set, or every
  // tick re-subscribes it for ever and the streams pile up.
  const after = await manager.reattachLostStreams();
  assert.deepEqual(after, { reattached: [], stillLost: [] });
});

test("a terminal that never lost its stream is not re-subscribed", async () => {
  // The control. A reconciler that re-attached everything every tick would open a second stream per
  // terminal per tick, which is worse than the deafness it is fixing.
  let subscribeCalls = 0;
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput() { subscribeCalls += 1; return () => {}; },
    async stop() { return { ok: true }; },
    async list() { return { ok: true, handle: { processes: [{ id: "env-x" }] } }; },
  };
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true, client } });
  await manager.start({
    id: "t-fine", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });
  assert.equal(subscribeCalls, 1);
  const result = await manager.reattachLostStreams();
  assert.deepEqual(result, { reattached: [], stillLost: [] });
  assert.equal(subscribeCalls, 1, "a healthy terminal was re-subscribed, doubling its stream");
});

test("with delegation off there is nothing to re-attach", async () => {
  const manager = new TerminalProcessManager({ envDelegation: null });
  assert.deepEqual(await manager.reattachLostStreams(), { reattached: [], stillLost: [] });
});
