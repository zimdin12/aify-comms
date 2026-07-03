#!/usr/bin/env node
// Regression: the reaper must NEVER signal the bridge itself, its process
// group, its launching shell, or init.
//
// Incident (2026-07-03): the environment bridge exited during its boot survivor
// sweep on the 1st/2nd launch (stable by the 3rd, once the orphan backlog
// drained). Root cause: the reaper kill paths — `killPid`, `terminateProcessTree`
// (which does `process.kill(-pid)` = a whole-process-GROUP kill), and
// `defaultKillPid` — validated only `pid > 0`. A group-kill whose pgid collided
// with the bridge's session, or a recycled tracked pid that now mapped to the
// bridge/parent shell, delivered SIGTERM to the bridge → graceful teardown →
// exit. `process.kill(0)` (caller's whole group) and `process.kill(1)` (init)
// were also unguarded. Same family as the 2026-05-31 cross-contamination that
// killed the operator's own session.
//
// Fix: `pidIsSelfProtected(pid)` guards every reaper kill. This test pins that
// the guard refuses all self/group/parent/init targets (in positive AND negative
// forms) while still allowing an unrelated pid — and that calling the real kill
// helper on our own pid is a no-op (this test process must survive it).
import assert from "node:assert/strict";
import { pidIsSelfProtected } from "../runtimes-process.js";
import { defaultKillPid } from "../reap-managed-claude.js";

// Our own process group id (Linux), used to assert the group-kill guard.
let ownPgid = null;
try {
  const { readFileSync } = await import("node:fs");
  const stat = readFileSync("/proc/self/stat", "utf8");
  ownPgid = Number(stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/)[2]) || null;
} catch { /* non-Linux: pgid-specific assertions are skipped below */ }

// ── Protected targets: the guard must return true ────────────────────────────
assert.equal(pidIsSelfProtected(process.pid), true, "own pid must be protected");
assert.equal(pidIsSelfProtected(-process.pid), true, "own pid as a GROUP kill (-pid) must be protected");
assert.equal(pidIsSelfProtected(0), true, "pid 0 (caller's whole process group) must be protected");
assert.equal(pidIsSelfProtected(1), true, "pid 1 (init) must be protected");
assert.equal(pidIsSelfProtected(-1), true, "pid -1 (every process we may signal) must be protected");
if (process.ppid > 1) {
  assert.equal(pidIsSelfProtected(process.ppid), true, "launching shell (ppid) must be protected");
  assert.equal(pidIsSelfProtected(-process.ppid), true, "ppid as a group kill must be protected");
}
if (ownPgid) {
  assert.equal(pidIsSelfProtected(ownPgid), true, "own process group id must be protected");
  assert.equal(pidIsSelfProtected(-ownPgid), true, "own pgid as a group kill (-pgid) must be protected");
}

// Non-numeric / garbage is refused (never signal something we can't reason about).
assert.equal(pidIsSelfProtected("nope"), true, "non-numeric pid must be refused");
assert.equal(pidIsSelfProtected(NaN), true, "NaN pid must be refused");
assert.equal(pidIsSelfProtected(undefined), true, "undefined pid must be refused");

// ── An unrelated pid is NOT protected (the guard must not neuter the reaper) ──
// Pick a high pid that is not us, our parent, or our group.
const unrelated = Math.max(process.pid, process.ppid, ownPgid || 0) + 100000;
assert.equal(pidIsSelfProtected(unrelated), false, "an unrelated pid must remain reap-able");

// ── The real kill helper is a no-op on our own pid (we must survive it) ───────
assert.equal(defaultKillPid(process.pid), false, "defaultKillPid must refuse our own pid");
assert.equal(defaultKillPid(0), false, "defaultKillPid must refuse pid 0");
assert.equal(defaultKillPid(1), false, "defaultKillPid must refuse init");
// If the guard regressed, the SIGTERM above would have killed this process and
// we would never reach here.
assert.ok(process.pid > 0, "test process survived the self-kill attempts");

console.log("reaper-self-protect.test.js: all assertions passed");
