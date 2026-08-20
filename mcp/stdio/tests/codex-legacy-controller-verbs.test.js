// CodexLegacyController's control verbs — the app-server path's interrupt and steer.
//
// Fourteenth cluster off the V8-coverage census and the last of the controller family: `injectMessage`,
// `interrupt` and `steer` had a zero call count. This is the controller every resident, channel and
// unrecognised-mode codex run lands on, so these three are the Stop and the mid-turn append for a live
// codex thread.
//
// WHAT IS DIFFERENT ABOUT THIS ONE. The other controllers delegate their controls to a session object. This
// one speaks the app-server RPC itself, which puts three decisions in its own code:
//
//   * interrupt with NO live turn falls back to killing the app-server process TREE. There is no turn to
//     cancel, and leaving the process would leak a codex app-server per cancelled dispatch.
//   * interrupt's RPC failure is reported by REJECTING THE RUN, not by throwing at the caller. The operator
//     asked for the turn to stop; if the interrupt could not be delivered, the run is what is broken.
//   * steer carries `expectedTurnId`, which is what keeps a mid-turn append from landing on the NEXT turn if
//     the current one ended between the read and the send.
//
// NOT COVERED HERE, and the census will still list them: `startThread`, `get rpc` and `get proc` live inside
// the closure `start()` builds, so nothing can reach them without acquiring a real app-server. They need a
// start()-level harness, which is its own slice.

import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";

import { CodexLegacyController } from "../controllers/codex-legacy-controller.js";

function makeController({ threadId = "th_1", turnId = "turn_1", rpc, proc = null } = {}) {
  const controller = new CodexLegacyController({ callbacks: {} });
  controller._activeThreadId = threadId;
  controller._ctx = { activeTurnId: turnId };
  controller._rpc = rpc || { request: async () => ({}) };
  controller._proc = proc;
  return controller;
}

const isAlive = (pid) => {
  try { process.kill(pid, 0); return true; } catch (err) { return !!err && err.code === "EPERM"; }
};

// A detached stand-in for the codex app-server, with a child of its own so a TREE is what gets asserted.
// Both pids are ours - no pid is ever chosen by heuristic, and none is passed to the real tree-killer unless
// this test created it.
// DETACHED ONLY OFF WINDOWS. A detached child on Windows gets its OWN CONSOLE, and windowsHide
// suppresses the window being SHOWN rather than the console being created -- with Windows Terminal
// as the host that surfaces as a tab that steals focus. The operator has now reported it three
// times; the previous fix added windowsHide, which treated the symptom.
//
// The fidelity is kept where it is free: on POSIX `detached` makes the child a process GROUP
// LEADER, which is the thing under test. On Windows the tree kill goes through `taskkill /T`,
// which walks the parent-child table and does not care about groups -- so dropping detached there
// costs the test nothing and stops opening windows on a working machine.
const DETACH = process.platform !== "win32";

async function spawnStandInAppServer() {
  const parent = spawn(process.execPath, [
    "-e",
    'const { spawn } = require("node:child_process");' +
    'const kid = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"],' +
    '  { stdio: "ignore", detached: ' + DETACH + ', windowsHide: true });' +
    'process.stdout.write(String(kid.pid) + "\\n");' +
    "setInterval(() => {}, 1000);",
  ], { detached: DETACH, stdio: ["ignore", "pipe", "ignore"], windowsHide: true });

  const childPid = await new Promise((resolve, reject) => {
    const bail = setTimeout(() => reject(new Error("the stand-in app-server never reported its child")), 10000);
    parent.stdout.on("data", (chunk) => {
      const line = String(chunk).trim();
      if (line) { clearTimeout(bail); resolve(Number(line)); }
    });
    parent.on("error", (err) => { clearTimeout(bail); reject(err); });
  });

  assert.ok(isAlive(parent.pid) && isAlive(childPid), "the fixture tree was not alive");
  return {
    proc: { pid: parent.pid },
    parentPid: parent.pid,
    childPid,
    async waitUntilDead(pid, timeoutMs = 10000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline && isAlive(pid)) {
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return !isAlive(pid);
    },
    cleanup() {
      try { process.kill(childPid, "SIGKILL"); } catch { /* already gone */ }
      try { parent.kill("SIGKILL"); } catch { /* already gone */ }
    },
  };
}

// ── inject ──────────────────────────────────────────────────────────────────

test("codex legacy refuses an inject and names the alternative", async () => {
  await assert.rejects(() => makeController().injectMessage({ text: "hi" }),
    /codex legacy does not support direct message injection; send a follow-up dispatch/);
});

// ── interrupt ───────────────────────────────────────────────────────────────

test("interrupt sends turn/interrupt for the live thread and turn", async () => {
  const calls = [];
  const controller = makeController({
    rpc: { request: async (method, params, timeout) => { calls.push([method, params, timeout]); return {}; } },
  });

  await controller.interrupt({});
  assert.deepEqual(calls, [["turn/interrupt", { threadId: "th_1", turnId: "turn_1" }, 30000]]);
  assert.equal(controller._interrupted, true);
});

test("the interrupt flag is set BEFORE anything can fail", async () => {
  // `_interrupted` is what the completion path reads to report the turn as interrupted rather than finished
  // (codex-legacy-controller.js:339). Setting it after the RPC would mean a cancelled turn that failed to
  // cancel gets reported as a normal completion - the operator's Stop vanishing from the record.
  const controller = makeController({
    rpc: { request: async () => { throw new Error("app-server gone"); } },
  });
  controller._rejectPromise = () => {};
  await controller.interrupt({});
  assert.equal(controller._interrupted, true);
});

test("an interrupt RPC failure REJECTS THE RUN rather than throwing at the caller", async () => {
  // The control call itself must resolve: the operator's Stop was accepted and acted on. What failed is the
  // run, and that is where the error belongs - otherwise a failed interrupt surfaces as a broken button while
  // the run sits there looking healthy.
  const rejections = [];
  const controller = makeController({
    rpc: { request: async () => { throw new Error("turn/interrupt timed out"); } },
  });
  controller._rejectPromise = (err) => { rejections.push(err); };

  await assert.doesNotReject(() => controller.interrupt({}));
  assert.equal(rejections.length, 1);
  assert.match(rejections[0].message, /turn\/interrupt timed out/);
});

test("an interrupt RPC failure with no run to reject is still not thrown", async () => {
  // `_rejectPromise` is only wired once start() has built the run promise. A control arriving before that must
  // not turn into an unhandled rejection inside the bridge's control loop.
  const controller = makeController({ rpc: { request: async () => { throw new Error("nope"); } } });
  controller._rejectPromise = null;
  await assert.doesNotReject(() => controller.interrupt({}));
});

test("with no live turn, interrupt sends no RPC at all", async () => {
  for (const [label, patch] of [
    ["no thread", { threadId: "" }],
    ["no turn", { turnId: null }],
    ["neither", { threadId: "", turnId: null }],
  ]) {
    const calls = [];
    const controller = makeController({
      ...patch,
      rpc: { request: async (method) => { calls.push(method); return {}; } },
    });
    await controller.interrupt({});
    assert.deepEqual(calls, [], `${label}: an RPC was sent for a turn that does not exist`);
    assert.equal(controller._interrupted, true, `${label}: the interrupt was forgotten`);
  }
});

test("with no live turn, interrupt kills the app-server process TREE", async () => {
  // The leak this prevents: a cancelled dispatch whose app-server was already spawned. Nothing else in this
  // path reaps it, so an interrupt-before-first-turn would leave one codex app-server per attempt.
  const fixture = await spawnStandInAppServer();
  try {
    const controller = makeController({ threadId: "", turnId: null, proc: fixture.proc });
    await controller.interrupt({});
    assert.ok(await fixture.waitUntilDead(fixture.parentPid), "the app-server survived the interrupt");
    assert.ok(await fixture.waitUntilDead(fixture.childPid),
      "the app-server's child survived - the pid was reached but not the tree");
  } finally {
    fixture.cleanup();
  }
});

test("with a LIVE turn, interrupt cancels the turn and leaves the app-server running", async () => {
  // The other half, and the one that matters more. Killing the process tree here would destroy the whole
  // thread instead of cancelling one turn - for a resident codex agent that is the operator's live
  // conversation, gone, because they pressed Stop.
  const fixture = await spawnStandInAppServer();
  try {
    const calls = [];
    const controller = makeController({
      proc: fixture.proc,
      rpc: { request: async (method) => { calls.push(method); return {}; } },
    });
    await controller.interrupt({});

    assert.deepEqual(calls, ["turn/interrupt"], "the turn was not cancelled through the app-server");
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.equal(isAlive(fixture.parentPid), true, "the app-server was killed for a turn-level cancel");
    assert.equal(isAlive(fixture.childPid), true, "the app-server's child was killed for a turn-level cancel");
  } finally {
    fixture.cleanup();
  }
});

test("a process-tree kill that throws does not fail the interrupt", async () => {
  // Wrapped in its own try/catch: a stale pid is the normal case here, and a Stop must not report failure
  // because the thing it was cleaning up had already exited.
  const controller = makeController({ threadId: "", turnId: null, proc: { get pid() { throw new Error("stale"); } } });
  await assert.doesNotReject(() => controller.interrupt({}));
});

// ── steer ───────────────────────────────────────────────────────────────────

test("steer sends turn/steer with the text and the EXPECTED turn id", async () => {
  // `expectedTurnId` is the anti-race field: if the turn ended between reading it and sending, the append must
  // be refused by the app-server rather than land on whatever turn is running now.
  const calls = [];
  const events = [];
  const controller = makeController({
    rpc: { request: async (method, params, timeout) => { calls.push([method, params, timeout]); return {}; } },
  });
  controller.opts.callbacks.onEvent = (kind, text) => { events.push([kind, text]); };

  await controller.steer("more context");
  assert.deepEqual(calls, [[
    "turn/steer",
    { threadId: "th_1", input: [{ type: "text", text: "more context" }], expectedTurnId: "turn_1" },
    30000,
  ]]);
  assert.deepEqual(events, [["steer", "Steer applied to turn_1"]]);
});

test("steer with no live turn says so instead of sending an RPC", async () => {
  for (const patch of [{ threadId: "" }, { turnId: null }]) {
    const calls = [];
    const controller = makeController({ ...patch, rpc: { request: async (m) => { calls.push(m); return {}; } } });
    await assert.rejects(() => controller.steer("text"), /No active Codex turn to steer/);
    assert.deepEqual(calls, [], "an RPC was sent for a turn that does not exist");
  }
});

test("an EMPTY steer is refused before it reaches the app-server", async () => {
  // An empty append is not a no-op to codex - it is a turn/steer with no content. Refusing here keeps the
  // failure in the caller's hands with a message that says what was missing.
  for (const body of ["", "   ", "\t\n", null, undefined]) {
    const calls = [];
    const controller = makeController({ rpc: { request: async (m) => { calls.push(m); return {}; } } });
    await assert.rejects(() => controller.steer(body), /Steer body is required/,
      `${JSON.stringify(body)} was accepted`);
    assert.deepEqual(calls, []);
  }
});

test("steer does not swallow an app-server rejection", async () => {
  // Opposite of interrupt, deliberately: a steer that failed did NOT happen, and the caller is the one that
  // has to know - there is nothing to clean up and nothing already done.
  const controller = makeController({
    rpc: { request: async () => { throw new Error("turn/steer rejected: turn already ended"); } },
  });
  await assert.rejects(() => controller.steer("text"), /turn already ended/);
});

test("steer survives a missing onEvent callback", async () => {
  // `opts.callbacks` is optional at construction; the event emit is best-effort and must not undo a steer that
  // the app-server has already accepted.
  const controller = new CodexLegacyController({});
  controller._activeThreadId = "th_1";
  controller._ctx = { activeTurnId: "turn_1" };
  controller._rpc = { request: async () => ({}) };
  await assert.doesNotReject(() => controller.steer("text"));
});
