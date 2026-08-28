// A Stop that did not land does not report that it did.
//
// THE ORPHAN FACTORY BY A THIRD ROUTE, found by following what happens to a HELD terminal when
// somebody presses Stop. The first two were an unobserved stream end read as an exit, and a held
// terminal nothing could ever re-attach or finalise. This one is simpler and worse:
//
//     stop(id) -> terminal.term.kill() -> dispatch("kill", () => client.stop(id))
//
// `dispatch` is the shim's FIRE-AND-FORGET path -- deliberately, because `write` and `resize` have no
// caller to answer. It reports a refusal to `console.error` and returns nothing. So `stop()` deleted
// the terminal from its map and returned `{ stopped: true }` whatever happened.
//
// A Stop pressed while aify-env was down therefore left the process RUNNING, the row `stopped`, and
// this bridge with no memory of it. Nothing would reap it, nothing would re-attach it, and the
// operator would be told it stopped.
//
// AND THE EXISTING FALLBACK MADE IT WORSE, not better. The control loop reads `{ stopped: false }` as
// "this bridge never owned the PTY" and kills the persisted pid directly. For a delegated terminal
// that pid is aify-env's child: killing it behind aify-env's back leaves its registry holding an entry
// for a process that no longer exists, in the one tier that exists so a host has ONE owner per
// process. So the honest answer had to carry which case it was.
import assert from "node:assert/strict";
import { test } from "node:test";

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { TerminalProcessManager } from "../terminal-runtime.js";
import { orphanPidToKill } from "../terminal-control.js";

// ---- the fallback's decision --------------------------------------------------------------------

test("a stop that worked needs no fallback", () => {
  assert.equal(orphanPidToKill({ stopped: true }, { pid: 4242 }), 0);
});

test("a PTY this bridge never owned is still reaped by pid", () => {
  // The case the fallback was built for, unchanged: the owning bridge died and left a console running
  // that nobody else can reach.
  assert.equal(orphanPidToKill({ stopped: false }, { pid: 4242 }), 4242);
});

test("a DELEGATED stop that failed is never reaped by pid", () => {
  // aify-env is alive enough to have refused. Killing its child behind its back leaves its registry
  // wrong, in the tier whose whole purpose is that a host has one owner per process.
  assert.equal(
    orphanPidToKill({ stopped: false, delegated: true, error: "aify-env refused the stop" }, { pid: 4242 }),
    0,
    "a delegated process was killed by pid behind the environment that owns it",
  );
});

test("the delegated flag only counts when it is exactly true", () => {
  // A truthy-but-not-true value arriving from elsewhere must not silently disable the fallback for a
  // genuinely orphaned PTY -- that would trade this defect for the one the fallback exists to fix.
  for (const value of ["yes", 1, {}]) {
    assert.equal(orphanPidToKill({ stopped: false, delegated: value }, { pid: 4242 }), 4242,
      `delegated: ${JSON.stringify(value)} disabled the fallback`);
  }
});

test("no usable pid means no fallback, delegated or not", () => {
  for (const control of [{}, { pid: 0 }, { pid: -1 }, { pid: "x" }]) {
    assert.equal(orphanPidToKill({ stopped: false }, control), 0);
  }
});

// ---- the stop itself, driven through the manager -------------------------------------------------

/**
 * A real launcher on disk: shebang plus the marker aify-env requires. Same fixture shape as
 * `delegated-terminal-controls.test.js`, and for the reason recorded there -- `process.execPath` is
 * not a launcher, aify-env would refuse it, and a test spawning one passes on a spawn the
 * environment could never accept.
 */
function writeLauncherFixture(name) {
  const dir = mkdtempSync(join(tmpdir(), "aify-launcher-"));
  const file = join(dir, name);
  const eol = String.fromCharCode(10);
  writeFileSync(file, ["#!/usr/bin/env bash", 'HARNESS_WRAPPER_VERSION="0.6.0"', "exit 0", ""].join(eol));
  return file;
}

test("a delegated stop that aify-env REFUSES reports failure and keeps the terminal", async () => {
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput() { return () => {}; },
    async stop() { return { ok: false, error: "aify-env unreachable" }; },
    async list() { return { ok: true, handle: { processes: [{ id: "env-x" }] } }; },
  };
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true, client } });
  await manager.start({
    id: "t-stop", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });

  const result = await manager.stop("t-stop");
  assert.equal(result.stopped, false, "a refused stop reported success");
  assert.equal(result.delegated, true, "the caller cannot tell this from an unowned PTY");
  assert.ok(
    manager.terminals.has("t-stop"),
    "the terminal was forgotten despite its process still running -- that is the orphan",
  );
});

test("a delegated stop that lands reports success and forgets the terminal", async () => {
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput() { return () => {}; },
    async stop() { return { ok: true }; },
    async list() { return { ok: true, handle: { processes: [] } }; },
  };
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true, client } });
  await manager.start({
    id: "t-ok", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });
  const result = await manager.stop("t-ok");
  assert.equal(result.stopped, true);
  assert.ok(!manager.terminals.has("t-ok"));
});

test("a 404 from aify-env IS a stop: the process is already gone", async () => {
  const client = {
    async start() { return { ok: true, handle: { id: "env-x", pid: 99, terminal: true } }; },
    async subscribeOutput() { return () => {}; },
    async stop() { return { ok: false, status: 404, error: "no such process: env-x" }; },
    async list() { return { ok: true, handle: { processes: [] } }; },
  };
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true, client } });
  await manager.start({
    id: "t-gone", command: writeLauncherFixture("probe-aify"),
    argv: [writeLauncherFixture("probe-aify"), "--version"],
    cwd: process.cwd(), runtime: "claude-code", sessionMode: "managed",
  });
  const result = await manager.stop("t-gone");
  assert.equal(result.stopped, true, "an already-gone process was reported as a failed stop");
  assert.ok(!manager.terminals.has("t-gone"));
});
