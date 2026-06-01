#!/usr/bin/env node
// Kill-by-pid stop fallback (2026-06-02): persist the PTY root pid + reap an
// orphaned console by pid when the owning bridge is gone. Tests inject the
// tree-killer; no real processes are spawned or killed.
import assert from "node:assert/strict";
import { TerminalProcessManager } from "../terminal-runtime.js";
import { orphanPidToKill } from "../terminal-control.js";

// --- TerminalProcessManager.killByPid routes to the (injected) tree-killer ---
{
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, onExit: async () => {} });
  const killed = [];
  mgr._reapPtyTree = (proc) => { killed.push(proc?.pid); };

  const res = mgr.killByPid(43210);
  assert.deepEqual(res, { killed: true }, "killByPid should report killed for a valid pid");
  assert.deepEqual(killed, [43210], "killByPid must pass the pid to the tree-killer");

  // Guards: non-positive / non-integer / missing pids never reach the killer.
  killed.length = 0;
  for (const bad of [0, -1, NaN, undefined, null, "", "abc", 1.5]) {
    const r = mgr.killByPid(bad);
    assert.deepEqual(r, { killed: false }, `killByPid(${String(bad)}) should be a no-op`);
  }
  assert.deepEqual(killed, [], "killByPid must not invoke the killer for invalid pids");
}

// --- orphanPidToKill: the stop-control fallback decision (pure) ---
{
  // Map-miss stop (bridge never owned the PTY) + a pid → reap that pid.
  assert.equal(
    orphanPidToKill({ stopped: false }, { pid: 55501 }),
    55501,
    "Map-miss stop with a pid must select the orphan pid for kill-by-pid",
  );

  // Owned-in-memory stop (the unchanged path) → NEVER kill-by-pid.
  assert.equal(
    orphanPidToKill({ stopped: true }, { pid: 55501 }),
    0,
    "owned stop must NOT trigger kill-by-pid",
  );

  // Map-miss but no/invalid pid → nothing to reap.
  assert.equal(orphanPidToKill({ stopped: false }, {}), 0, "Map-miss without a pid is a no-op");
  assert.equal(orphanPidToKill({ stopped: false }, { pid: 0 }), 0, "pid 0 is invalid");
  assert.equal(orphanPidToKill({ stopped: false }, { pid: "abc" }), 0, "non-numeric pid is invalid");
}

// --- Integration: a real Map-miss stop() drives the fallback; owned does not ---
{
  // (a) Orphan: the manager does NOT hold this terminal, so stop() returns
  // {stopped:false}; the fallback then kills by the control pid.
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, onExit: async () => {} });
  const killed = [];
  mgr._reapPtyTree = (proc) => { killed.push(proc?.pid); };

  const orphanControl = { pid: 99001 };
  const stopResult = await mgr.stop("term-orphan", "test");
  assert.equal(stopResult.stopped, false, "stop() on an unowned terminal must be a Map-miss");
  const pidToKill = orphanPidToKill(stopResult, orphanControl);
  if (pidToKill) mgr.killByPid(pidToKill);
  assert.deepEqual(killed, [99001], "orphaned-console stop must reap the control pid");

  // (b) Owned: stop() reaps the in-memory PTY (returns {stopped:true}); the
  // kill-by-pid fallback must NOT fire even though a pid is present.
  killed.length = 0;
  mgr.terminals.set("term-owned", {
    id: "term-owned", kind: "pty", exitPromise: Promise.resolve(),
    // No real pid: terminateProcessTree() early-returns on a pid-less proc, so
    // stop()'s owned PTY teardown touches no real process. We only need stop()
    // to report {stopped:true} so the fallback-skip path is exercised.
    term: { kill: () => {} },
  });
  const ownedResult = await mgr.stop("term-owned", "test");
  assert.equal(ownedResult.stopped, true, "stop() on an owned terminal must report stopped:true");
  const ownedPidToKill = orphanPidToKill(ownedResult, { pid: 12321 });
  if (ownedPidToKill) mgr.killByPid(ownedPidToKill);
  assert.deepEqual(killed, [], "owned stop must NOT invoke kill-by-pid fallback");
}

console.log("terminal-kill-by-pid.test.js: all assertions passed");
